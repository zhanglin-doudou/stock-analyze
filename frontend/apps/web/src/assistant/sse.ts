export type SseEvent = {
  event: string;
  data: unknown;
  id?: string;
};

/** 轻量 SSE 解析器：支持 event/data/多行 data/CRLF/注释，按空行切分。 */
export async function* parseSse(response: Response): AsyncGenerator<SseEvent> {
  if (!response.body) throw new Error("SSE 响应没有 body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseBlock(block);
      if (event) yield event;
      boundary = buffer.indexOf("\n\n");
    }
  }
  buffer = (buffer + decoder.decode()).replace(/\r\n/g, "\n");
  const tail = parseBlock(buffer);
  if (tail) yield tail;
}

function parseBlock(block: string): SseEvent | null {
  const lines = block.replace(/\r\n/g, "\n").split("\n");
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon >= 0 ? line.slice(0, colon) : line;
    let value = colon >= 0 ? line.slice(colon + 1) : "";
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  const raw = data.join("\n");
  try {
    return id === undefined
      ? { event, data: JSON.parse(raw) as unknown }
      : { event, data: JSON.parse(raw) as unknown, id };
  } catch {
    return id === undefined ? { event, data: raw } : { event, data: raw, id };
  }
}
