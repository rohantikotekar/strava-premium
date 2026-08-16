/**
 * Client-side zip sniffing — entirely local, no upload.
 *
 * Reads only the central directory (the file listing at the end of a zip),
 * a few KB even for a multi-GB archive, via `File.slice()`. This is what lets
 * us reject "that's not a Strava export" before the user waits through a
 * multi-gigabyte upload only to have the server tell them (INGESTION.md §2).
 *
 * Deliberately hand-rolled rather than a zip library: we only ever need to
 * read filenames out of the central directory, never decompress anything.
 */

const LOCAL_FILE_HEADER_SIG = 0x04034b50;
const EOCD_SIG = 0x06054b50;
const CENTRAL_DIR_SIG = 0x02014b50;
//: End Of Central Directory record is 22 bytes plus up to a 64KB comment —
// scan that much of the tail to find it regardless of comment length.
const EOCD_SEARCH_WINDOW = 22 + 65535;

export type ZipCheck =
  | { ok: true }
  | { ok: false; reason: "not_a_zip" }
  | { ok: false; reason: "missing_activities_csv" };

async function readBytes(file: File, start: number, end: number): Promise<DataView> {
  const buffer = await file.slice(Math.max(0, start), end).arrayBuffer();
  return new DataView(buffer);
}

function findEocd(view: DataView): number | null {
  // Scan backward — the comment (if any) sits between the signature and EOF,
  // so a forward scan could match comment bytes that happen to look like it.
  for (let i = view.byteLength - 22; i >= 0; i--) {
    if (view.getUint32(i, true) === EOCD_SIG) return i;
  }
  return null;
}

/**
 * Best-effort: confirms this is a zip and, when it can, that it contains an
 * `activities.csv` entry. Never hard-blocks on something it can't parse (a
 * ZIP64 archive, an unusual layout) — those fall through as "ok" and get the
 * real check server-side, so a legitimate large export is never falsely
 * rejected by this shortcut.
 */
export async function checkStravaExportZip(file: File): Promise<ZipCheck> {
  const header = await readBytes(file, 0, 4);
  if (file.size < 22 || header.getUint32(0, true) !== LOCAL_FILE_HEADER_SIG) {
    return { ok: false, reason: "not_a_zip" };
  }

  try {
    const tail = await readBytes(file, file.size - EOCD_SEARCH_WINDOW, file.size);
    const eocdOffset = findEocd(tail);
    if (eocdOffset === null) return { ok: true }; // inconclusive — let the server decide

    const entryCount = tail.getUint16(eocdOffset + 10, true);
    const centralDirSize = tail.getUint32(eocdOffset + 12, true);
    const centralDirOffset = tail.getUint32(eocdOffset + 16, true);

    // 0xFFFFFFFF markers mean ZIP64 — our reader doesn't parse the ZIP64
    // locator, so don't guess; leave it to the server.
    if (centralDirOffset === 0xffffffff || centralDirSize === 0xffffffff) {
      return { ok: true };
    }

    const centralDir = await readBytes(file, centralDirOffset, centralDirOffset + centralDirSize);

    let cursor = 0;
    const decoder = new TextDecoder();
    for (let entry = 0; entry < entryCount && cursor + 46 <= centralDir.byteLength; entry++) {
      if (centralDir.getUint32(cursor, true) !== CENTRAL_DIR_SIG) break;
      const nameLength = centralDir.getUint16(cursor + 28, true);
      const extraLength = centralDir.getUint16(cursor + 30, true);
      const commentLength = centralDir.getUint16(cursor + 32, true);

      const nameBytes = new Uint8Array(
        centralDir.buffer,
        centralDir.byteOffset + cursor + 46,
        Math.min(nameLength, centralDir.byteLength - cursor - 46),
      );
      const name = decoder.decode(nameBytes).toLowerCase();
      if (name === "activities.csv" || name.endsWith("/activities.csv")) {
        return { ok: true };
      }

      cursor += 46 + nameLength + extraLength + commentLength;
    }

    return { ok: false, reason: "missing_activities_csv" };
  } catch {
    // A slice/read failure (odd browser, truncated local file) is not
    // evidence the archive is bad — don't block on our own reader's limits.
    return { ok: true };
  }
}
