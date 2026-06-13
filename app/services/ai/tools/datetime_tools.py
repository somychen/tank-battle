"""时间/日历/节假日工具 — 离线计算（免费）"""

from __future__ import annotations

from datetime import datetime

from .base import BaseTool, ToolDefinition, ToolParameter


class DateTimeTool(BaseTool):

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_datetime_info",
            description="获取当前时间、日期、星期、农历、中国节假日等信息。当用户询问时间、日期、节日、假期相关问题时使用。",
            parameters=[
                ToolParameter(
                    name="query_type",
                    type="string",
                    description="查询类型",
                    enum=["now", "today", "week_info", "month_info", "holidays"],
                ),
            ],
        )

    async def execute(self, arguments: dict) -> str:
        query_type = arguments.get("query_type", "now")

        if query_type == "now":
            return self._get_now()

        elif query_type == "today":
            return self._get_today()

        elif query_type == "week_info":
            return self._get_week_info()

        elif query_type == "month_info":
            return self._get_month_info()

        elif query_type == "holidays":
            return self._get_holidays()

        return f"未知查询类型: {query_type}"

    @staticmethod
    def _get_now() -> str:
        now = datetime.now()
        return (
            f"当前时间: {now.strftime('%Y年%m月%d日 %H:%M:%S')}\n"
            f"星期: {_weekday_cn(now.weekday())}\n"
            f"时区: 北京时间 (UTC+8)"
        )

    @staticmethod
    def _get_today() -> str:
        now = datetime.now()
        lines = [
            f"日期: {now.strftime('%Y年%m月%d日')}",
            f"星期: {_weekday_cn(now.weekday())}",
            f"农历: {_get_lunar(now)}",
        ]

        # 中国节假日
        holiday_info = _get_holiday_info(now)
        if holiday_info:
            lines.append(holiday_info)

        return "\n".join(lines)

    @staticmethod
    def _get_week_info() -> str:
        now = datetime.now()
        weekday = now.weekday()
        monday = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        monday -= timedelta(days=weekday)

        lines = [f"本周: {monday.strftime('%m月%d日')} - {(monday + timedelta(days=6)).strftime('%m月%d日')}"]
        for i in range(7):
            d = monday + timedelta(days=i)
            marker = " ← 今天" if d.date() == now.date() else ""
            info = _get_holiday_info(d)
            extra = f" ({info})" if info else ""
            lines.append(f"  {_weekday_cn(i)} {d.strftime('%m月%d日')}{marker}{extra}")
        return "\n".join(lines)

    @staticmethod
    def _get_month_info() -> str:
        import calendar
        now = datetime.now()
        cal = calendar.TextCalendar(calendar.SUNDAY)
        return f"{now.year}年{now.month}月:\n" + cal.formatmonth(now.year, now.month)

    @staticmethod
    def _get_holidays() -> str:
        try:
            from chinese_calendar import get_holidays, Holiday
            now = datetime.now()
            year_start = datetime(now.year, 1, 1)
            year_end = datetime(now.year + 1, 3, 1)
            holidays = get_holidays(year_start, year_end)

            if not holidays:
                return f"{now.year}年暂无节假日数据。"

            lines = [f"{now.year}年中国节假日:"]
            for h in holidays:
                if isinstance(h, tuple):
                    date_obj, name = h
                    lines.append(f"  {date_obj.strftime('%m月%d日')} {name}")
                else:
                    lines.append(f"  {h}")
            return "\n".join(lines)
        except ImportError:
            return "节假日数据不可用（缺少 chinese_calendar 库）。"


# ---- 辅助函数 ----

def _weekday_cn(weekday: int) -> str:
    names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return names[weekday]


def _get_lunar(dt: datetime) -> str:
    try:
        from lunardate import LunarDate
        lunar = LunarDate.fromSolarDate(dt.year, dt.month, dt.day)
        gan = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
        zhi = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
        shengxiao = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
        lunar_months = ["正月", "二月", "三月", "四月", "五月", "六月",
                        "七月", "八月", "九月", "十月", "冬月", "腊月"]
        lunar_days = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
                      "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
                      "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]

        year_gan = gan[(lunar.year - 4) % 10]
        year_zhi = zhi[(lunar.year - 4) % 12]
        animal = shengxiao[(lunar.year - 4) % 12]
        month_cn = lunar_months[lunar.month - 1] if lunar.month <= 12 else "闰" + lunar_months[(lunar.month - 1) % 12]
        day_cn = lunar_days[lunar.day - 1] if lunar.day <= 30 else "三十"

        return f"{year_gan}{year_zhi}年({animal}) {month_cn}{day_cn}"
    except ImportError:
        return "农历数据不可用"


def _get_holiday_info(dt: datetime) -> str:
    try:
        from chinese_calendar import is_holiday, is_workday, get_holiday_detail
        if is_holiday(dt.date()):
            detail = get_holiday_detail(dt.date())
            return f"🎉 节假日: {detail if detail else '休息日'}"
        elif is_workday(dt.date()):
            return "工作日"
        return ""
    except ImportError:
        return ""
