from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from difflib import SequenceMatcher
from astrbot.api import logger
import re # 导入 re 模块


@register("thefuck", "vmoranv", "一个类似 thefuck 的插件", "1.0.0")
class TheFuckPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.threshold = self.config.get("threshold", 0.6)
        # 修改 last_messages 结构，存储元组 (command_part, full_message)
        self.last_messages: dict[str, tuple[str, str]] = {}

    @filter.command("fuck")
    async def fuck_command(self, event: AstrMessageEvent):
        session_id = event.session_id
        # 获取存储的元组
        stored_data = self.last_messages.get(session_id)

        if not stored_data:
            logger.info(f"会话 {session_id} 未找到上一条消息")
            yield event.plain_result("未找到上一条消息")
            return

        # 解包元组
        last_command_part, last_full_message = stored_data
        logger.debug(f"会话 {session_id} 的上一条命令部分: '{last_command_part}', 完整消息: '{last_full_message}'")

        commands = self.get_all_commands()
        logger.debug(f"获取到的所有命令: {commands}")
        # 使用命令部分进行匹配
        best_match = self.find_best_match(last_command_part, commands)
        logger.debug(f"命令部分 '{last_command_part}' 的最佳匹配: {best_match}")

        if best_match and best_match[1] >= self.threshold:
            corrected_command_base = best_match[0] # 正确的命令，如 /pixiv
            # 从原始完整消息中提取参数
            original_parts = last_full_message.split(' ', 1)
            original_args = original_parts[1] if len(original_parts) > 1 else ""

            # 组合建议的完整命令
            suggested_full_command = f"{corrected_command_base} {original_args}".strip() # strip() 避免没参数时末尾多空格
            logger.info(f"建议命令: {suggested_full_command}")
            yield event.plain_result(f"你是不是想输入: {suggested_full_command}")
        else:
            logger.info("未找到足够相似的匹配命令")
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

        if not message_content.startswith('/fuck'):
            # 按第一个空格分割命令和参数
            parts = message_content.split(' ', 1)
            command_part = parts[0]
            # 存储命令部分和完整消息的元组
            logger.debug(f"为会话 {session_id} 存储命令部分: '{command_part}', 完整消息: '{message_content}'")
            self.last_messages[session_id] = (command_part, message_content)
        else:
            logger.debug(f"忽略 /fuck 命令消息: {message_content}")

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
                                    valid_cmds = [f"/{cmd}" for cmd in command_names if isinstance(cmd, str) and cmd.strip()]
                                    if valid_cmds:
                                        logger.info(f"      添加命令 (来自装饰器): {valid_cmds}")
                                        commands.extend(valid_cmds)
                                    else:
                                        logger.info(f"      命令列表 {command_names} (来自装饰器) 中所有命令均为空或非字符串，已跳过")
                                elif isinstance(command_names, str):
                                     if command_names.strip():
                                         cmd_to_add = f"/{command_names.strip()}"
                                         logger.info(f"      添加命令 (来自装饰器): ['{cmd_to_add}']")
                                         commands.append(cmd_to_add)
                                     else:
                                         logger.info(f"      单个命令字符串 '{command_names}' (来自装饰器) 为空，已跳过")
                                else:
                                     logger.warning(f"插件 {plugin_name} 的命令 {attr_name} 的 command_names (来自装饰器) 类型不受支持: {type(command_names)}")
                        else:
                             logger.info(f"  属性 {attr_name} 的 __command_filter__ 为 None")

                    # 如果没有通过装饰器找到，并且属性是可调用的，则假定方法名是命令
                    if not found_by_decorator and attr and callable(attr):
                         assumed_cmd = f"/{attr_name}"
                         logger.info(f"  检测到可调用公共属性 '{attr_name}'，假定为命令: {assumed_cmd}")
                         commands.append(assumed_cmd)

            except Exception as e:
                logger.error(f"处理插件 {plugin_name} 的属性时出错: {e}")
                continue

        unique_commands = list(set(commands))
        if "/fuck" in unique_commands:
             logger.info("从最终列表中移除 /fuck 命令")
             unique_commands.remove("/fuck")

        logger.info(f"最终提取到的唯一命令列表 (包含推断): {unique_commands}")
        return unique_commands

    def find_best_match(self, message: str, commands: list) -> tuple | None:
        best_match = None
        best_ratio = 0

        if not commands:
            logger.warning("命令列表为空，无法进行匹配")
            return None

        for cmd in commands:
            if not isinstance(cmd, str):
                logger.warning(f"命令列表包含非字符串元素: {cmd}")
                continue
            # 确保匹配时比较的是纯命令部分
            ratio = SequenceMatcher(None, message.split(' ', 1)[0], cmd).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = (cmd, ratio)

        return best_match