/**
 * The day planner for the built-in demo backend. Plain safety rules in code (no AI):
 * it turns an hourly forecast into a 30-minute plan for each person.
 *
 *   1. Work hours 7:00-17:00. On a hot day (heat index peak 100 °F or more) start at 6:30.
 *   2. Lunch break 12:00-12:30 for everyone.
 *   3. Breaks get closer together as it gets hotter:
 *        heat index under 91 °F   every 2 h
 *        91-102 °F                every 90 min
 *        103 °F and up            every hour
 *      Breaks are staggered so the whole crew never stops at once.
 *   4. No heavy work when the heat index is 103 °F or more (light tasks only).
 *      From 91 °F, heavy work becomes moderate.
 *   5. Heavy work goes in the cool hours.
 *   6. When storms are likely (thunder in the forecast, or 60 %+ rain), indoor / truck work.
 *   7. Someone who had a heat warning in the last 24 h gets one step lighter work in the heat.
 *
 * checkPlan() re-checks rules 3 and 4 on the finished plan. Lightning stop-work and
 * heat-alert changes during the day are layered on top by the engine.
 */

import type { TaskKind } from "../lib/contract.ts";

export interface HourWx {
  hour: number;          // 0-23, local time
  hiF: number;           // NWS heat index, °F
  rain: number;          // % chance
  thunder: boolean;
}

export interface PlanPerson { id: string; name: string; breakOffset: number; lighter: boolean }

export interface PlanSlot { start: number; task: TaskKind; label: string }

export interface DayPlan {
  slots: Record<string, PlanSlot[]>;     // per person, 30-min slots from 6:00 to 18:00
  notes: string[];                        // why the plan looks like this, with the real numbers
  startHour: number;                      // 6.5 or 7
  peakHiF: number;
  peakHour: number;
  heatWindow: [number, number] | null;    // hours with heat index >= 91 °F
  stormWindow: [number, number] | null;   // hours with storms likely
  checked: boolean;                       // passed checkPlan()
}

export const LABEL: Record<TaskKind, string> = {
  heavy: "Heavy: line work",
  moderate: "Moderate: equipment",
  light: "Light: inspection",
  break: "Break: shade + water",
  stop_work: "Stop work: lightning",
  off: "Off",
};
const STORM_LABEL = "Indoor / truck work (storms likely)";

const DAY_FROM = 6, DAY_TO = 18;               // slots cover 6:00-18:00
const fmtHour = (h: number) => {
  const hh = Math.floor(h), mm = Math.round((h - hh) * 60);
  return `${((hh + 11) % 12) + 1}${mm ? `:${String(mm).padStart(2, "0")}` : ""} ${hh < 12 ? "AM" : "PM"}`;
};

function window(hours: HourWx[], test: (h: HourWx) => boolean): [number, number] | null {
  const hit = hours.filter((h) => h.hour >= 7 && h.hour < 17 && test(h)).map((h) => h.hour);
  return hit.length ? [Math.min(...hit), Math.max(...hit) + 1] : null;
}

/** Plan one day. `dayStart` = Unix seconds of midnight local time. */
export function planDay(dayStart: number, hours: HourWx[], people: PlanPerson[], source: string): DayPlan {
  const at = (hour: number) => hours.find((h) => h.hour === Math.floor(hour)) ?? { hour, hiF: 80, rain: 0, thunder: false };
  const work = hours.filter((h) => h.hour >= 7 && h.hour < 17);
  const peak = work.reduce((a, b) => (b.hiF > a.hiF ? b : a), work[0] ?? { hour: 14, hiF: 80, rain: 0, thunder: false });
  const startHour = peak.hiF >= 100 ? 6.5 : 7;
  const heatWindow = window(hours, (h) => h.hiF >= 91);
  const dangerWindow = window(hours, (h) => h.hiF >= 103);
  const storm = (h: HourWx) => h.thunder || h.rain >= 60;
  const stormWindow = window(hours, storm);

  const slots: DayPlan["slots"] = {};
  for (const p of people) {
    const list: PlanSlot[] = [];
    let sinceBreak = p.breakOffset % 3;       // stagger
    for (let h = DAY_FROM; h < DAY_TO; h += 0.5) {
      const start = dayStart + h * 3600;
      const wx = at(h);
      let task: TaskKind;
      let label: string | null = null;
      if (h < startHour || h >= 17) task = "off";
      else if (h === 12) { task = "break"; sinceBreak = 0; }
      else {
        const every = wx.hiF >= 103 ? 2 : wx.hiF >= 91 ? 3 : 4;          // slots between breaks
        if (sinceBreak >= every - 1) { task = "break"; sinceBreak = 0; }
        else {
          sinceBreak++;
          if (storm(wx)) { task = "light"; label = STORM_LABEL; }
          else if (wx.hiF >= 103) task = "light";
          else if (wx.hiF >= 91) task = p.lighter ? "light" : "moderate";
          else task = h < 12 ? "heavy" : "moderate";                    // heavy while it's cool, moderate after lunch
        }
      }
      list.push({ start, task, label: label ?? LABEL[task] });
    }
    slots[p.id] = list;
  }

  const notes: string[] = [];
  notes.push(`Heat index peaks at ${Math.round(peak.hiF)} °F around ${fmtHour(peak.hour)} (${source}).`);
  if (startHour < 7) notes.push("Hot day: start at 6:30 AM to get the heavy work done early.");
  if (heatWindow) {
    const heavyEnd = Math.min(heatWindow[0], 12);
    notes.push(`Heavy work from ${fmtHour(startHour)} to ${fmtHour(heavyEnd)}, while it's coolest.`);
    notes.push(`Heat index 91 °F or more from ${fmtHour(heatWindow[0])} to ${fmtHour(heatWindow[1])}: breaks every ${dangerWindow ? "60 to 90" : "90"} minutes.`);
  } else {
    notes.push("No heat window today: regular breaks every 2 hours.");
  }
  if (dangerWindow) notes.push(`No heavy work from ${fmtHour(dangerWindow[0])} to ${fmtHour(dangerWindow[1])} (heat index 103 °F or more).`);
  if (stormWindow) {
    const worst = hours.filter((h) => h.hour >= stormWindow[0] && h.hour < stormWindow[1]);
    const rain = Math.max(...worst.map((h) => h.rain));
    notes.push(`Storms likely ${fmtHour(stormWindow[0])} to ${fmtHour(stormWindow[1])} (${rain}% rain${worst.some((h) => h.thunder) ? ", thunder" : ""}): indoor or truck work. If lightning comes within 10 miles, everyone stops.`);
  }
  for (const p of people.filter((x) => x.lighter)) notes.push(`${p.name}: lighter work in the heat (had a heat warning in the last 24 h).`);

  const plan: DayPlan = {
    slots, notes, startHour, peakHiF: Math.round(peak.hiF), peakHour: peak.hour, heatWindow, stormWindow, checked: false,
  };
  plan.checked = checkPlan(plan, hours);
  return plan;
}

/** Rules 3 and 4, checked on the finished plan: no heavy work at 103 °F+, and nobody works too long between breaks. */
export function checkPlan(plan: DayPlan, hours: HourWx[]): boolean {
  const hi = (start: number) => hours.find((h) => h.hour === new Date(start * 1000).getHours())?.hiF ?? 80;
  for (const list of Object.values(plan.slots)) {
    let run = 0;
    for (const s of list) {
      if (s.task === "heavy" && hi(s.start) >= 103) return false;
      if (s.task === "break" || s.task === "off" || s.task === "stop_work") { run = 0; continue; }
      run++;
      const limit = hi(s.start) >= 103 ? 2 : hi(s.start) >= 91 ? 3 : 4;
      if (run > limit) return false;
    }
  }
  return true;
}

/**
 * A recorded hot July day at Deerhaven (sample data for the video replay):
 * a humid afternoon peaking near 110 °F heat index, with thunderstorms from 3 PM.
 */
export const RECORDED_JULY_DAY: { hour: number; tempF: number; rh: number; rain: number; thunder: boolean }[] = [
  { hour: 6, tempF: 77, rh: 90, rain: 0, thunder: false }, { hour: 7, tempF: 79, rh: 86, rain: 0, thunder: false },
  { hour: 8, tempF: 82, rh: 80, rain: 0, thunder: false }, { hour: 9, tempF: 85, rh: 73, rain: 5, thunder: false },
  { hour: 10, tempF: 88, rh: 67, rain: 5, thunder: false }, { hour: 11, tempF: 90, rh: 63, rain: 10, thunder: false },
  { hour: 12, tempF: 92, rh: 60, rain: 15, thunder: false }, { hour: 13, tempF: 94, rh: 57, rain: 20, thunder: false },
  { hour: 14, tempF: 95, rh: 55, rain: 35, thunder: false }, { hour: 15, tempF: 93, rh: 60, rain: 65, thunder: true },
  { hour: 16, tempF: 88, rh: 72, rain: 70, thunder: true }, { hour: 17, tempF: 85, rh: 78, rain: 50, thunder: false },
  { hour: 18, tempF: 83, rh: 82, rain: 30, thunder: false }, { hour: 19, tempF: 81, rh: 85, rain: 10, thunder: false },
  { hour: 20, tempF: 80, rh: 87, rain: 5, thunder: false },
];
