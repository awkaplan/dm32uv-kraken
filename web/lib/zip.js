// A small ZIP writer for the diagnostics download (lib/diag.js). Written here because the page loads no
// third-party code. Each file is deflated with the browser's own CompressionStream("deflate-raw") when it
// has one, else stored. No DOM, so Node tests can import it.

import { concat } from "./bytes.js";
import { crc32 } from "./kr.js";

const le16 = (n) => [n & 0xff, (n >>> 8) & 0xff];
const le32 = (n) => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff];

async function deflateRaw(data) {
  if (typeof CompressionStream !== "function") return null;
  try {
    const stream = new Blob([data]).stream().pipeThrough(new CompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch {
    return null; // a browser without "deflate-raw": store the file instead
  }
}

function dosTime(d) {
  const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
  const date = ((Math.max(d.getFullYear(), 1980) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  return { time, date };
}

// files: [{ name, data: string | Uint8Array }] -> the .zip as a Uint8Array
export async function zip(files, { date = new Date(), compress = true } = {}) {
  const enc = new TextEncoder();
  const { time, date: day } = dosTime(date);
  const locals = [];
  const central = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const raw = typeof f.data === "string" ? enc.encode(f.data) : f.data;
    const crc = crc32(raw);
    const packed = compress ? await deflateRaw(raw) : null;
    const useDeflate = packed !== null && packed.length < raw.length;
    const body = useDeflate ? packed : raw;
    const method = useDeflate ? 8 : 0;
    // version 2.0, flag bit 11: the name is UTF-8
    const common = [...le16(20), ...le16(0x0800), ...le16(method), ...le16(time), ...le16(day),
      ...le32(crc), ...le32(body.length), ...le32(raw.length), ...le16(name.length), ...le16(0)];
    const local = concat(Uint8Array.from([...le32(0x04034b50), ...common]), name, body);
    central.push(concat(Uint8Array.from([...le32(0x02014b50), ...le16(20), ...common,
      ...le16(0), ...le16(0), ...le16(0), ...le32(0), ...le32(offset)]), name));
    locals.push(local);
    offset += local.length;
  }
  const dir = concat(...central);
  const end = Uint8Array.from([...le32(0x06054b50), ...le16(0), ...le16(0), ...le16(files.length), ...le16(files.length),
    ...le32(dir.length), ...le32(offset), ...le16(0)]);
  return concat(...locals, dir, end);
}
