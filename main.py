import asyncio
import re
from datetime import datetime
from typing import Optional, List

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import Plain, At


class ReminderRule:
    def __init__(
        self,
        rule_id: str,
        user_id: str,
        user_name: str = "",
        group_id: str = "",
        mode: str = "timed",
        enabled: bool = True,
        repeat_type: str = "daily",
        time_point: Optional[str] = None,
        time_start: Optional[str] = None,
        time_end: Optional[str] = None,
        reminder_text: str = "",
        reply_text: str = "",
        unified_msg_origin: Optional[str] = None,
        last_triggered_date: Optional[str] = None,
        rule_name: Optional[str] = None,
    ):
        self.rule_id = rule_id
        self.user_id = user_id
        self.user_name = user_name
        self.group_id = group_id
        self.mode = mode
        self.enabled = enabled
        self.repeat_type = repeat_type
        self.time_point = time_point
        self.time_start = time_start
        self.time_end = time_end
        self.reminder_text = reminder_text
        self.reply_text = reply_text
        self.unified_msg_origin = unified_msg_origin
        self.last_triggered_date = last_triggered_date
        self.rule_name = rule_name

    def to_dict(self):
        return {
            "rule_id": self.rule_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "group_id": self.group_id,
            "mode": self.mode,
            "enabled": self.enabled,
            "repeat_type": self.repeat_type,
            "time_point": self.time_point,
            "time_start": self.time_start,
            "time_end": self.time_end,
            "reminder_text": self.reminder_text,
            "reply_text": self.reply_text,
            "unified_msg_origin": self.unified_msg_origin,
            "last_triggered_date": self.last_triggered_date,
            "rule_name": self.rule_name,
        }

    @staticmethod
    def from_dict(data):
        return ReminderRule(
            rule_id=data.get("rule_id", ""),
            user_id=data.get("user_id", ""),
            user_name=data.get("user_name", ""),
            group_id=data.get("group_id", ""),
            mode=data.get("mode", "timed"),
            enabled=data.get("enabled", True),
            repeat_type=data.get("repeat_type", "daily"),
            time_point=data.get("time_point"),
            time_start=data.get("time_start"),
            time_end=data.get("time_end"),
            reminder_text=data.get("reminder_text", ""),
            reply_text=data.get("reply_text", ""),
            unified_msg_origin=data.get("unified_msg_origin"),
            last_triggered_date=data.get("last_triggered_date"),
            rule_name=data.get("rule_name"),
        )


def get_ats(event: AstrMessageEvent):
    ats = []
    for seg in event.get_messages():
        if isinstance(seg, At):
            ats.append({"id": str(seg.qq), "name": getattr(seg, 'name', None) or str(seg.qq)})
    return ats


def parse_time(time_str: str):
    try:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            return None
        hour, minute = int(parts[0]), int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
        return None
    except:
        return None


class AutoReminderSupervisorPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.rules: List[ReminderRule] = []
        self.scheduler_task: Optional[asyncio.Task] = None
        self._running = False
        self._load_rules()
        logger.info(f"自动提醒监督助手插件已加载，共 {len(self.rules)} 条规则")

    def _load_rules(self):
        timed_rules = self.config.get("timed_reminders", [])
        supervision_rules = self.config.get("supervision_rules", [])

        self.rules = []

        for r in timed_rules:
            if not r.get("user_id"):
                logger.warning(f"定时提醒规则缺少user_id，已跳过: {r}")
                continue
            rule = ReminderRule.from_dict(r)
            if not rule.rule_id:
                rule.rule_id = self._generate_rule_id()
            self.rules.append(rule)

        for r in supervision_rules:
            if not r.get("user_id"):
                logger.warning(f"监督规则缺少user_id，已跳过: {r}")
                continue
            rule = ReminderRule.from_dict(r)
            if not rule.rule_id:
                rule.rule_id = self._generate_rule_id()
            self.rules.append(rule)

    def _save_rules(self):
        try:
            timed_list = []
            supervision_list = []

            for r in self.rules:
                rule_dict = r.to_dict()
                rule_dict["__template_key"] = "timed" if r.mode == "timed" else "supervision"
                if r.mode == "timed":
                    timed_list.append(rule_dict)
                else:
                    supervision_list.append(rule_dict)

            self.config["timed_reminders"] = timed_list
            self.config["supervision_rules"] = supervision_list
            self.config.save_config()
            logger.info(f"规则已保存，定时提醒 {len(timed_list)} 条，持续监督 {len(supervision_list)} 条")
        except Exception as e:
            logger.error(f"保存规则失败: {e}")

    def _generate_rule_id(self):
        existing_ids = []
        for r in self.rules:
            try:
                existing_ids.append(int(r.rule_id))
            except:
                pass
        return str(max(existing_ids) + 1 if existing_ids else 1)

    def _time_in_range(self, current_time: datetime, start_str: str, end_str: str) -> bool:
        start = parse_time(start_str)
        end = parse_time(end_str)
        if not start or not end:
            return False
        start_mins = start[0] * 60 + start[1]
        end_mins = end[0] * 60 + end[1]
        current_mins = current_time.hour * 60 + current_time.minute

        if end_mins < start_mins:
            return current_mins >= start_mins or current_mins <= end_mins
        else:
            return start_mins <= current_mins <= end_mins

    async def _send_reminder(self, rule: ReminderRule, text: str):
        try:
            target_id = rule.unified_msg_origin or rule.group_id
            if not target_id:
                logger.warning(f"规则 {rule.rule_id} 没有发送目标")
                return

            from astrbot.api import message_components as Comp
            chain = MessageChain().message(f"@{rule.user_name} {text}")
            chain.chain = [Comp.At(qq=rule.user_id), Comp.Plain(f" {text}")]
            await self.context.send_message(target_id, chain)
            logger.info(f"提醒已发送: 规则 {rule.rule_id}")
        except Exception as e:
            logger.error(f"发送提醒失败: {e}")

    async def _scheduler_loop(self):
        logger.info("定时调度器已启动")
        while self._running:
            try:
                now = datetime.now()
                today_date = now.strftime("%Y-%m-%d")

                for rule in self.rules:
                    if not rule.enabled:
                        continue

                    if rule.mode == "timed" and rule.time_point:
                        parsed = parse_time(rule.time_point)
                        if not parsed:
                            continue

                        hour, minute = parsed
                        if now.hour == hour and now.minute == minute and now.second < 2:
                            should_trigger = False

                            if rule.repeat_type == "once":
                                if rule.last_triggered_date is None:
                                    should_trigger = True
                            elif rule.repeat_type == "daily":
                                if rule.last_triggered_date != today_date:
                                    should_trigger = True

                            if should_trigger:
                                rule.last_triggered_date = today_date
                                self._save_rules()
                                reminder_text = rule.reminder_text or "时间到了！"
                                await self._send_reminder(rule, reminder_text)
                                logger.info(f"定时提醒触发: 规则 {rule.rule_id} ({rule.time_point})")

                    elif rule.mode == "supervision" and rule.time_start and rule.time_end:
                        if self._time_in_range(now, rule.time_start, rule.time_end):
                            if rule.repeat_type == "once":
                                if rule.last_triggered_date is None:
                                    rule.last_triggered_date = today_date
                                    self._save_rules()
                                    logger.info(f"监督模式一次性触发: 规则 {rule.rule_id}")
                                else:
                                    if rule.last_triggered_date != today_date:
                                        rule.last_triggered_date = today_date
                                        self._save_rules()
                                        logger.info(f"监督模式一次性触发: 规则 {rule.rule_id}")

                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"调度器错误: {e}")
                await asyncio.sleep(5)

    @filter.command("rs")
    async def cmd_rs(self, event: AstrMessageEvent):
        self._load_rules()

        msg = event.message_str.strip()

        if msg == "" or msg == "rs":
            yield event.plain_result("""📋 自动提醒监督助手

命令格式:
/rs 帮助 - 查看帮助
/rs 列表 - 查看规则列表
/rs 添加提醒 @用户 HH:MM 内容 - 添加定时提醒
/rs 添加监督 @用户 HH:MM HH:MM 内容 - 添加持续监督
/rs 启用 规则ID... - 启用规则(支持多选)
/rs 禁用 规则ID... - 禁用规则(支持多选)
/rs 删除 规则ID... - 删除规则(支持多选)""")
            return

        if msg.startswith("rs 帮助") or msg == "rs help":
            yield event.plain_result("""📋 自动提醒监督助手 使用指南

【命令格式】
添加定时提醒:
/rs 添加提醒 @用户 HH:MM 内容 [一次性|每天]

添加持续监督:
/rs 添加监督 @用户 HH:MM HH:MM 内容 [一次性|每天]

查看规则列表:
/rs 列表

启用规则(支持多选):
/rs 启用 1 2 3

禁用规则(支持多选):
/rs 禁用 1 2 3

删除规则(支持多选):
/rs 删除 1 2 3

帮助:
/rs 帮助""")
            return

        if msg.startswith("rs 列表"):
            if not self.rules:
                yield event.plain_result("暂无监督规则")
                return

            timed_rules = [r for r in self.rules if r.mode == "timed"]
            supervision_rules = [r for r in self.rules if r.mode == "supervision"]

            lines = ["📋 监督规则列表:\n"]

            if timed_rules:
                lines.append("⏰ 定时提醒:")
                for rule in timed_rules:
                    status = "✅" if rule.enabled else "❌"
                    repeat_info = "🔁" if rule.repeat_type == "daily" else "⚡"
                    lines.append(f"  [{rule.rule_id}] {rule.rule_name or rule.user_name} | {rule.time_point} | {repeat_info} {status}")

            if supervision_rules:
                lines.append("\n👁️ 持续监督:")
                for rule in supervision_rules:
                    status = "✅" if rule.enabled else "❌"
                    repeat_info = "🔁" if rule.repeat_type == "daily" else "⚡"
                    lines.append(f"  [{rule.rule_id}] {rule.rule_name or rule.user_name} | {rule.time_start}-{rule.time_end} | {repeat_info} {status}")

            yield event.plain_result("\n".join(lines))
            return

        if msg.startswith("rs 启用 "):
            rule_ids = msg.replace("rs 启用 ", "").strip().split()
            if not rule_ids:
                yield event.plain_result("请指定规则ID")
                return

            enabled_count = 0
            for rule_id in rule_ids:
                for rule in self.rules:
                    if rule.rule_id == rule_id:
                        rule.enabled = True
                        enabled_count += 1
                        break

            self._save_rules()
            yield event.plain_result(f"✅ 已启用 {enabled_count} 条规则")
            return

        if msg.startswith("rs 禁用 "):
            rule_ids = msg.replace("rs 禁用 ", "").strip().split()
            if not rule_ids:
                yield event.plain_result("请指定规则ID")
                return

            disabled_count = 0
            for rule_id in rule_ids:
                for rule in self.rules:
                    if rule.rule_id == rule_id:
                        rule.enabled = False
                        disabled_count += 1
                        break

            self._save_rules()
            yield event.plain_result(f"✅ 已禁用 {disabled_count} 条规则")
            return

        if msg.startswith("rs 删除 "):
            rule_ids = msg.replace("rs 删除 ", "").strip().split()
            if not rule_ids:
                yield event.plain_result("请指定规则ID")
                return

            deleted_count = 0
            self.rules = [r for r in self.rules if r.rule_id not in rule_ids]
            deleted_count = len(rule_ids)

            self._save_rules()
            yield event.plain_result(f"✅ 已删除 {deleted_count} 条规则")
            return

        if msg.startswith("rs 添加提醒"):
            ats = get_ats(event)
            if not ats:
                yield event.plain_result("请指定用户，格式: @用户名")
                return

            target_user_id = ats[0]["id"]
            target_user_name = ats[0]["name"]

            pattern = r'rs 添加提醒\s+(@\S+\s+)?(\d{1,2}:\d{2})\s+(.+)?'
            match = re.match(pattern, msg, re.DOTALL)
            if not match:
                yield event.plain_result("格式错误！正确格式: /rs 添加提醒 @用户 HH:MM 内容")
                return

            time_str = match.group(2)
            if not parse_time(time_str):
                yield event.plain_result("时间格式错误，请使用 HH:MM 格式")
                return

            content = match.group(3).strip() if match.group(3) else "时间到了！"

            repeat_type = "daily"
            if "一次性" in content or "once" in content.lower():
                repeat_type = "once"
                content = re.sub(r'(一次性|once)', '', content).strip()

            group_id = event.get_group_id()
            unified_msg_origin = getattr(event, 'unified_msg_origin', None) or group_id

            rule = ReminderRule(
                rule_id=self._generate_rule_id(),
                user_id=target_user_id,
                user_name=target_user_name,
                group_id=group_id or "",
                mode="timed",
                enabled=True,
                repeat_type=repeat_type,
                time_point=time_str,
                reminder_text=content,
                unified_msg_origin=unified_msg_origin,
                rule_name=f"提醒-{target_user_name}",
            )
            self.rules.append(rule)
            self._save_rules()

            repeat_desc = "一次性" if repeat_type == "once" else "每天"
            yield event.plain_result(
                f"✅ 定时提醒已添加！\n规则ID: {rule.rule_id}\n用户: {target_user_name}\n时间: {time_str}\n重复: {repeat_desc}\n内容: {content}"
            )
            return

        if msg.startswith("rs 添加监督"):
            ats = get_ats(event)
            if not ats:
                yield event.plain_result("请指定用户，格式: @用户名")
                return

            target_user_id = ats[0]["id"]
            target_user_name = ats[0]["name"]

            pattern = r'rs 添加监督\s+(@\S+\s+)?(\d{1,2}:\d{2})\s+(\d{1,2}:\d{2})\s+(.+)?'
            match = re.match(pattern, msg, re.DOTALL)
            if not match:
                yield event.plain_result("格式错误！正确格式: /rs 添加监督 @用户 HH:MM HH:MM 内容")
                return

            time_start = match.group(2)
            time_end = match.group(3)

            if not parse_time(time_start) or not parse_time(time_end):
                yield event.plain_result("时间格式错误，请使用 HH:MM 格式")
                return

            content = match.group(4).strip() if match.group(4) else "为什么还在发消息？"

            repeat_type = "daily"
            if "一次性" in content or "once" in content.lower():
                repeat_type = "once"
                content = re.sub(r'(一次性|once)', '', content).strip()

            group_id = event.get_group_id()
            unified_msg_origin = getattr(event, 'unified_msg_origin', None) or group_id

            rule = ReminderRule(
                rule_id=self._generate_rule_id(),
                user_id=target_user_id,
                user_name=target_user_name,
                group_id=group_id or "",
                mode="supervision",
                enabled=True,
                repeat_type=repeat_type,
                time_start=time_start,
                time_end=time_end,
                reply_text=content,
                unified_msg_origin=unified_msg_origin,
                rule_name=f"监督-{target_user_name}",
            )
            self.rules.append(rule)
            self._save_rules()

            repeat_desc = "一次性" if repeat_type == "once" else "每天"
            yield event.plain_result(
                f"✅ 持续监督已添加！\n规则ID: {rule.rule_id}\n用户: {target_user_name}\n时间段: {time_start}-{time_end}\n重复: {repeat_desc}\n回复: {content}"
            )
            return

        yield event.plain_result("未知命令，输入 /rs 查看帮助")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if not self._running:
            return

        sender_id = event.get_sender_id()
        group_id = event.get_group_id()

        if not group_id or not sender_id:
            return

        msg = event.message_str.strip()
        if msg.startswith("/rs") or msg.startswith("rs"):
            return

        now = datetime.now()

        for rule in self.rules:
            if not rule.enabled:
                continue
            if rule.user_id != sender_id or rule.group_id != group_id:
                continue
            if rule.mode != "supervision":
                continue
            if not (rule.time_start and rule.time_end):
                continue

            if self._time_in_range(now, rule.time_start, rule.time_end):
                reply_text = rule.reply_text or "为什么还不睡？"

                if rule.repeat_type == "once":
                    if rule.last_triggered_date == now.strftime("%Y-%m-%d"):
                        continue
                    rule.last_triggered_date = now.strftime("%Y-%m-%d")
                    self._save_rules()

                yield event.chain_result([At(qq=sender_id), Plain(f" {reply_text}")])
                break

    async def initialize(self):
        self._running = True
        self._load_rules()
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("自动提醒监督助手插件已启动")

    async def terminate(self):
        self._running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
        logger.info("自动提醒监督助手插件已关闭")
