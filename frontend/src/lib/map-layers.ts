/** Which lightning rings the map draws around a site (plain TS so tests can check the rule). */
import type { Hazard } from "./contract.ts";

export const TEN_MILES_M = 16093;     // stop-work radius (PROJECT.md: 10-mile rule)
export const THIRTY_MILES_M = 48280;  // watch radius (session C1)

export function lightningRings(h: Hazard | undefined): { kind: "stop" | "watch"; radius_m: number }[] {
  if (!h || h.kind !== "lightning" || h.level === "none") return [];
  const rings: { kind: "stop" | "watch"; radius_m: number }[] = [{ kind: "watch", radius_m: THIRTY_MILES_M }];
  if (h.stop_work) rings.push({ kind: "stop", radius_m: TEN_MILES_M });
  return rings;
}
