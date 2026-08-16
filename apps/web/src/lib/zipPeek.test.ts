// jsdom's Blob/File implementation doesn't implement `.slice().arrayBuffer()`
// (a long-standing jsdom gap, not a code bug) — happy-dom does, and this file
// is the only one that needs real File/Blob byte-reading behaviour.
// @vitest-environment happy-dom

import { describe, expect, it } from "vitest";
import { checkStravaExportZip } from "./zipPeek";

/** Builds a minimal, valid, uncompressed (store-method) zip in memory —
 * enough structure for zipPeek's reader, without pulling in a zip library. */
function buildZip(entries: { name: string; content: string }[]): File {
  const encoder = new TextEncoder();
  const parts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;

  for (const { name, content } of entries) {
    const nameBytes = encoder.encode(name);
    const dataBytes = encoder.encode(content);

    const local = new Uint8Array(30 + nameBytes.length);
    const localView = new DataView(local.buffer);
    localView.setUint32(0, 0x04034b50, true); // local file header signature
    localView.setUint16(26, nameBytes.length, true); // filename length
    local.set(nameBytes, 30);

    parts.push(local, dataBytes);

    const central = new Uint8Array(46 + nameBytes.length);
    const centralView = new DataView(central.buffer);
    centralView.setUint32(0, 0x02014b50, true); // central dir signature
    centralView.setUint16(28, nameBytes.length, true); // filename length
    centralView.setUint32(24, dataBytes.length, true); // uncompressed size
    centralView.setUint32(42, offset, true); // local header offset
    central.set(nameBytes, 46);
    centralParts.push(central);

    offset += local.length + dataBytes.length;
  }

  const centralDirOffset = offset;
  const centralDirSize = centralParts.reduce((sum, p) => sum + p.length, 0);

  const eocd = new Uint8Array(22);
  const eocdView = new DataView(eocd.buffer);
  eocdView.setUint32(0, 0x06054b50, true);
  eocdView.setUint16(10, entries.length, true); // entries this disk
  eocdView.setUint16(8, entries.length, true); // total entries
  eocdView.setUint32(12, centralDirSize, true);
  eocdView.setUint32(16, centralDirOffset, true);

  return new File([...parts, ...centralParts, eocd] as BlobPart[], "test.zip", {
    type: "application/zip",
  });
}

describe("checkStravaExportZip", () => {
  it("accepts a zip containing activities.csv at the root", async () => {
    const zip = buildZip([{ name: "activities.csv", content: "id,date\n1,2026-01-01" }]);
    expect(await checkStravaExportZip(zip)).toEqual({ ok: true });
  });

  it("accepts activities.csv nested under a folder", async () => {
    const zip = buildZip([
      { name: "export_12345/activities.csv", content: "id,date" },
      { name: "export_12345/activities/1.fit", content: "binary" },
    ]);
    expect(await checkStravaExportZip(zip)).toEqual({ ok: true });
  });

  it("rejects a well-formed zip with no activities.csv", async () => {
    const zip = buildZip([{ name: "readme.txt", content: "hello" }]);
    expect(await checkStravaExportZip(zip)).toEqual({
      ok: false,
      reason: "missing_activities_csv",
    });
  });

  it("rejects a file that isn't a zip at all", async () => {
    const notAZip = new File(["just some plain text, not a zip"], "notes.zip", {
      type: "text/plain",
    });
    expect(await checkStravaExportZip(notAZip)).toEqual({ ok: false, reason: "not_a_zip" });
  });

  it("rejects a tiny file that can't possibly be a zip", async () => {
    const tiny = new File(["hi"], "tiny.zip");
    expect(await checkStravaExportZip(tiny)).toEqual({ ok: false, reason: "not_a_zip" });
  });

  it("does not false-positive on a filename that merely contains the string", async () => {
    const zip = buildZip([{ name: "not_activities.csv.bak", content: "x" }]);
    expect(await checkStravaExportZip(zip)).toEqual({
      ok: false,
      reason: "missing_activities_csv",
    });
  });
});
