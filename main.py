# 将 logger 导入移到最前面
from astrbot.api import logger

# 添加必要的 imports
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api.platform import AstrBotMessage, PlatformMetadata, MessageMember # 修正 MessageMember 导入
from astrbot.api.message_components import Plain # 从 message_components 只导入 Plain
from difflib import SequenceMatcher
import uuid # 导入 uuid 模块
import inspect # 导入 inspect 模块


@register("thefuck", "vmoranv", "一个类似 thefuck 的插件, 用于fuck错误命令并返回正确命令", "1.1.0")
class TheFuckPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.threshold = self.config.get("threshold", 0.6)
        # 配置的唤醒前缀列表
        self.wake_prefixes = self.config.get("wake_prefixes", ["/"])
        # 修改 last_messages 结构，存储元组 (user_prefix, command_name_without_prefix, full_message)
        self.last_messages: dict[str, tuple[str, str, str]] = {}
        # 添加一个新的字典来存储建议的命令
        self.suggested_commands: dict[str, str] = {}

    def extract_prefix(self, message: str) -> tuple[str, str] | None:
        """
        从消息中提取前缀和不带前缀的命令名。
        返回 (prefix, command_name_without_prefix) 或 None（如果未匹配任何前缀）
        """
        for prefix in self.wake_prefixes:
            if message.startswith(prefix):
                command_without_prefix = message[len(prefix):]
                return (prefix, command_without_prefix)
        return None

    @filter.command("fuck")
    async def fuck_command(self, event: AstrMessageEvent):
        session_id = event.session_id
        # 获取存储的元组
        stored_data = self.last_messages.get(session_id)

        if not stored_data:
            logger.info(f"会话 {session_id} 未找到上一条消息")
            yield event.plain_result("未找到上一条消息")
            return

        # 解包元组 (user_prefix, command_name_without_prefix, full_message)
        user_prefix, last_command_name, last_full_message = stored_data
        logger.debug(f"会话 {session_id} 的用户前缀: '{user_prefix}', 命令名: '{last_command_name}', 完整消息: '{last_full_message}'")

        commands = self.get_all_commands()
        logger.debug(f"获取到的所有命令（不带前缀）: {commands}")
        # 使用不带前缀的命令名进行匹配
        best_match = self.find_best_match(last_command_name, commands)
        logger.debug(f"命令名 '{last_command_name}' 的最佳匹配: {best_match}")

        if best_match and best_match[1] >= self.threshold:
            corrected_command_name = best_match[0] # 正确的命令名（不带前缀），如 pixiv
            # 从原始完整消息中提取参数（跳过前缀+命令部分）
            original_parts = last_full_message.split(' ', 1)
            original_args = original_parts[1] if len(original_parts) > 1 else ""

            # 使用用户的前缀组合建议的完整命令
            suggested_full_command = f"{user_prefix}{corrected_command_name} {original_args}".strip()
            logger.info(f"建议命令（使用用户前缀 '{user_prefix}'）: {suggested_full_command}")
            
            # 存储建议的命令，以便后续使用
            self.suggested_commands[session_id] = suggested_full_command
            
            # 返回建议，并提示用户可以输入Y/N确认
            yield event.plain_result(f"你是不是想输入: {suggested_full_command}\n输入Y/N确认")
        else:
            logger.info("未找到足够相似的匹配命令")
            # 清除之前的建议（如果有）
            if session_id in self.suggested_commands:
                del self.suggested_commands[session_id]
            yield event.plain_result("未找到匹配的命令")

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if event.message_obj and event.message_obj.self_id == event.get_sender_id():
            logger.debug("忽略机器人自身消息")
            return

        message_content = event.message_str.strip() # 去除首尾空格
        session_id = event.session_id

        # 忽略空消息
        if not message_content:
            logger.debug("忽略空消息")
            return
            
        # 处理用户对建议命令的确认
        if message_content.upper() == "Y" and session_id in self.suggested_commands:
            suggested_cmd = self.suggested_commands[session_id]
            logger.info(f"用户确认，尝试通过 platform.commit_event 提交伪造事件: '{suggested_cmd}'")

            # 清除建议
            if session_id in self.suggested_commands:
                 del self.suggested_commands[session_id]
                 logger.debug(f"已清除会话 {session_id} 的建议命令缓存")

            try:
                # 1. 获取平台名称和适配器
                platform_name = event.get_platform_name()
                platform = self.context.get_platform(platform_name)
                if not platform:
                    logger.error("无法从事件中获取平台名称")
                    yield event.plain_result("抱歉，内部错误，无法确定平台")
                    return

                # 2. 构造伪造的 AstrBotMessage
                fake_message = AstrBotMessage()
                fake_message.type = event.message_obj.type # 复制消息类型
                fake_message.message_str = suggested_cmd # 核心：修正后的命令
                # 构造发送者信息
                sender_id = event.get_sender_id()
                sender_name = event.get_sender_name()
                if sender_id:
                     fake_message.sender = MessageMember(user_id=sender_id, nickname=sender_name)
                     logger.debug(f"构造伪造消息发送者: ID={sender_id}, Name={sender_name}")
                else:
                     logger.warning("无法获取原始事件的发送者 ID，伪造消息将缺少发送者信息")
                fake_message.message = [Plain(text=suggested_cmd)] # 构造消息链
                # 设置 raw_message - 保持与原始事件一致或简化
                # 为了兼容性，最好复制原始 raw_message 并只修改必要部分
                # 但如果原始 raw_message 结构复杂或未知，可以创建一个简化的
                # 这里我们先尝试复制，如果 event.message_obj.raw_message 不可用则留空或简化
                try:
                    fake_message.raw_message = event.message_obj.raw_message
                    # 如果需要修改 raw_message 中的内容，在这里进行
                    # 例如: fake_message.raw_message['message'] = suggested_cmd
                except AttributeError:
                    logger.warning("原始事件的 message_obj 缺少 raw_message 属性，伪造消息的 raw_message 将为空")
                    fake_message.raw_message = {} # 或者根据平台构造一个最小化的

                fake_message.self_id = event.message_obj.self_id # 机器人自身 ID
                fake_message.session_id = event.session_id # 保持原始会话 ID
                # 添加 message_id
                fake_message.message_id = str(uuid.uuid4()) # 生成唯一的 message_id
                logger.debug(f"为伪造消息生成 message_id: {fake_message.message_id}")

                # 3. 创建与原始事件相同类型的伪造事件实例
                OriginalEventClass = event.__class__ # 获取原始事件的类
                logger.debug(f"将创建类型为 {OriginalEventClass.__name__} 的伪造事件")

                # 准备构造函数参数
                kwargs = {
                    "message_str": suggested_cmd,
                    "message_obj": fake_message,
                    "platform_meta": platform.meta(),
                    "session_id": event.session_id,
                }

                # 检查原始事件类的 __init__ 是否接受 'bot' 参数
                try:
                    sig = inspect.signature(OriginalEventClass.__init__)
                    if 'bot' in sig.parameters:
                        logger.debug(f"{OriginalEventClass.__name__}.__init__ 接受 'bot' 参数，将传递 event.bot")
                        kwargs['bot'] = event.bot
                    else:
                         logger.debug(f"{OriginalEventClass.__name__}.__init__ 不接受 'bot' 参数")
                except ValueError:
                    # 处理内置类型或无法获取签名的特殊情况 (虽然对于事件类不太可能)
                    logger.warning(f"无法获取 {OriginalEventClass.__name__}.__init__ 的签名")
                except Exception as inspect_err:
                    logger.error(f"检查 {OriginalEventClass.__name__}.__init__ 签名时出错: {inspect_err}", exc_info=True)


                # 使用获取到的类和准备好的参数创建实例
                fake_event = OriginalEventClass(**kwargs)
                logger.debug(f"成功创建伪造事件实例: {fake_event}")


                # 4. 提交伪造事件
                platform.commit_event(fake_event)
                logger.info(f"已通过 platform.commit_event 成功提交伪造事件 '{suggested_cmd}'")

            except AttributeError as ae:
                 logger.error(f"获取平台或创建/提交伪造事件时出错 (AttributeError): {ae}", exc_info=True)
                 yield event.plain_result(f"抱歉，处理确认时出错 (内部属性错误)")
            except Exception as e:
                # 捕获其他所有异常
                logger.error(f"通过 platform.commit_event 提交伪造事件 '{suggested_cmd}' 时发生意外错误: {e}", exc_info=True)
                yield event.plain_result(f"抱歉，尝试处理确认时发生意外错误: {e}")

            # 处理完 'Y' 的逻辑后结束
            return

        elif message_content.upper() == "N" and session_id in self.suggested_commands:
            # 用户拒绝，清除建议
            logger.info("用户拒绝执行建议命令")
            if session_id in self.suggested_commands: # 再次检查以防万一
                 del self.suggested_commands[session_id]
            yield event.plain_result("已取消")
            return # 处理完 Y/N 后结束

        # 检查是否是 fuck 命令（任意前缀 + fuck）
        is_fuck_command = False
        for prefix in self.wake_prefixes:
            if message_content.startswith(f"{prefix}fuck"):
                is_fuck_command = True
                break
        
        if not is_fuck_command and message_content.upper() not in ["Y", "N"]:
            # 提取前缀和命令名
            prefix_info = self.extract_prefix(message_content)
            if prefix_info:
                user_prefix, rest_of_message = prefix_info
                # rest_of_message 可能是 "helk args" 或 "helk"
                parts = rest_of_message.split(' ', 1)
                command_name = parts[0]  # 不带前缀的命令名
                logger.debug(f"为会话 {session_id} 存储: 前缀='{user_prefix}', 命令名='{command_name}', 完整消息='{message_content}'")
                self.last_messages[session_id] = (user_prefix, command_name, message_content)
            else:
                # 如果没有匹配到任何前缀，仍然存储但使用默认前缀
                parts = message_content.split(' ', 1)
                command_part = parts[0]
                default_prefix = self.wake_prefixes[0] if self.wake_prefixes else "/"
                logger.debug(f"未匹配前缀，为会话 {session_id} 存储: 默认前缀='{default_prefix}', 命令部分='{command_part}', 完整消息='{message_content}'")
                self.last_messages[session_id] = (default_prefix, command_part, message_content)
        elif is_fuck_command:
            logger.debug(f"忽略 fuck 命令消息，不更新 last_message: {message_content}")

    def get_all_commands(self) -> list:
        commands = []
        try:
            all_stars_metadata = self.context.get_all_stars()
        except AttributeError:
            logger.error("Context 对象缺少 get_all_stars 方法。请检查 AstrBot 版本或 API 文档。")
            return []
        except Exception as e:
            logger.error(f"调用 self.context.get_all_stars() 时出错: {e}")
            return []

        if not all_stars_metadata:
             logger.warning("未能从 self.context.get_all_stars() 获取到插件信息")
             return []

        logger.info(f"获取到 {len(all_stars_metadata)} 个插件元数据")

        for metadata in all_stars_metadata:
            plugin_name = getattr(metadata, 'name', '未知名称')
            plugin_instance = getattr(metadata, 'star_cls', None)
            logger.info(f"--- 正在处理插件: {plugin_name} (实例类型: {type(plugin_instance)}) ---")

            if not plugin_instance or not isinstance(plugin_instance, Star):
                 logger.warning(f"无法从元数据 {plugin_name} 获取有效的 Star 实例 (获取到类型: {type(plugin_instance)})")
                 continue

            if plugin_instance is self:
                logger.info(f"跳过插件自身: {plugin_name}")
                continue

            logger.info(f"检查插件 {plugin_name} 的命令...")
            try:
                attributes = dir(plugin_instance)
                for attr_name in attributes:
                    if attr_name.startswith('_'):
                        continue # 跳过私有/特殊属性

                    try:
                        attr = getattr(plugin_instance, attr_name, None)
                        logger.info(f"  检查属性: '{attr_name}', 类型: {type(attr)}")
                    except Exception as e_getattr:
                        logger.warning(f"  获取属性 '{attr_name}' 时出错: {e_getattr}")
                        continue

                    # 标记是否已通过 @filter.command 找到命令
                    found_by_decorator = False

                    # 优先检查 @filter.command 装饰器添加的标记
                    if attr and hasattr(attr, '__command_filter__'):
                        logger.info(f"  找到潜在命令处理器属性 (通过装饰器): {attr_name}")
                        command_filter = getattr(attr, '__command_filter__', None)
                        if command_filter:
                            command_names = getattr(command_filter, 'commands', None)
                            logger.info(f"    提取到的 command_names: {command_names}")
                            if command_names:
                                found_by_decorator = True # 标记已找到
                                if isinstance(command_names, (list, tuple)):
                                    # 不添加前缀，只保留命令名
                                    valid_cmds = [cmd for cmd in command_names if isinstance(cmd, str) and cmd.strip()]
                                    if valid_cmds:
                                        logger.info(f"      添加命令（不带前缀）(来自装饰器): {valid_cmds}")
                                        commands.extend(valid_cmds)
                                    else:
                                        logger.info(f"      命令列表 {command_names} (来自装饰器) 中所有命令均为空或非字符串，已跳过")
                                elif isinstance(command_names, str):
                                     if command_names.strip():
                                         cmd_to_add = command_names.strip()  # 不添加前缀
                                         logger.info(f"      添加命令（不带前缀）(来自装饰器): ['{cmd_to_add}']")
                                         commands.append(cmd_to_add)
                                     else:
                                         logger.info(f"      单个命令字符串 '{command_names}' (来自装饰器) 为空，已跳过")
                                else:
                                     logger.warning(f"插件 {plugin_name} 的命令 {attr_name} 的 command_names (来自装饰器) 类型不受支持: {type(command_names)}")
                        else:
                             logger.info(f"  属性 {attr_name} 的 __command_filter__ 为 None")

                    # 如果没有通过装饰器找到，并且属性是可调用的，则假定方法名是命令
                    if not found_by_decorator and attr and callable(attr):
                         assumed_cmd = attr_name  # 不添加前缀
                         logger.info(f"  检测到可调用公共属性 '{attr_name}'，假定为命令（不带前缀）: {assumed_cmd}")
                         commands.append(assumed_cmd)

            except Exception as e:
                logger.error(f"处理插件 {plugin_name} 的属性时出错: {e}")
                continue

        unique_commands = list(set(commands))
        if "fuck" in unique_commands:
             logger.info("从最终列表中移除 fuck 命令")
             unique_commands.remove("fuck")

        logger.info(f"最终提取到的唯一命令列表（不带前缀）(包含推断): {unique_commands}")
        return unique_commands

    def find_best_match(self, command_name: str, commands: list) -> tuple | None:
        """
        查找最佳匹配的命令。
        
        Args:
            command_name: 用户输入的命令名（不带前缀）
            commands: 可用命令列表（不带前缀）
        
        Returns:
            (matched_command, ratio) 或 None
        """
        best_match = None
        best_ratio = 0

        if not commands:
            logger.warning("命令列表为空，无法进行匹配")
            return None

        for cmd in commands:
            if not isinstance(cmd, str):
                logger.warning(f"命令列表包含非字符串元素: {cmd}")
                continue
            # 直接比较不带前缀的命令名
            ratio = SequenceMatcher(None, command_name, cmd).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = (cmd, ratio)

        return best_match