import dayjs from "dayjs";
import "dayjs/locale/zh-cn";

dayjs.locale("zh-cn");

/** 对话/历史统一时间格式：同一天只显示时分，跨年显示完整日期。 */
export function formatChatTime(value: string | number | Date): string {
  const time = dayjs(value);
  if (!time.isValid()) return String(value);
  if (time.isSame(dayjs(), "day")) return time.format("HH:mm");
  if (time.isSame(dayjs(), "year")) return time.format("MM-DD HH:mm");
  return time.format("YYYY-MM-DD HH:mm");
}

export function formatChatDateTime(value: string | number | Date): string {
  const time = dayjs(value);
  return time.isValid() ? time.format("YYYY-MM-DD HH:mm:ss") : String(value);
}
