/**
 * A specimen of the board for the sign-in screen, drawn from the same tokens
 * the real grid uses: tier tints, the left rule, the lunch band, the
 * moved-block amber.
 *
 * The layout is hard-coded rather than generated — a random scatter reads as
 * noise, where a real board has structure: columns that run full, a room that
 * goes quiet after lunch, one block the last replan moved.
 */

const ROOMS = 6;
const ROWS = 10;
const BREAK_AFTER = 5;   // lunch sits between rows 5 and 6

const GUTTER = 30;
const COL_W = 46;
const ROW_H = 15;
const HEAD_H = 15;
const BREAK_H = 9;

/** [column, row, rows spanned, tier, company] */
type Blk = [number, number, number, 1 | 2 | 3, string];

const BLOCKS: Blk[] = [
  [0, 0, 2, 3, "Optiver"], [0, 2, 2, 3, "DEShaw"], [0, 4, 2, 1, "TCS"],
  [0, 6, 2, 3, "Rubrik"], [0, 8, 2, 3, "Swiggy"],
  [1, 0, 2, 3, "OpenAI"], [1, 2, 1, 2, "Oracle"], [1, 3, 2, 3, "Zomato"],
  [1, 6, 2, 3, "Google"], [1, 8, 2, 2, "HCLTech"],
  [2, 0, 1, 2, "Oracle"], [2, 1, 2, 3, "Intel"], [2, 3, 2, 3, "NVIDIA"],
  [2, 6, 1, 1, "Wipro"], [2, 7, 2, 3, "Swiggy"],
  [3, 0, 2, 3, "Flipkart"], [3, 2, 2, 1, "Infosys"], [3, 4, 2, 3, "Zomato"],
  [3, 6, 2, 3, "OpenAI"], [3, 8, 2, 3, "Intel"],
  [4, 0, 2, 2, "Oracle"], [4, 2, 2, 3, "Google"], [4, 4, 2, 3, "Rubrik"],
  [4, 7, 2, 3, "DEShaw"],
  [5, 0, 1, 3, "Optiver"], [5, 1, 2, 2, "HCLTech"], [5, 3, 2, 3, "NVIDIA"],
  [5, 8, 2, 1, "Cognizant"],
];

/** The one block a replan moved. Short company name on purpose — it is the
    only block carrying a status glyph, and a longer name collides with it. */
const MOVED: Blk = [4, 6, 1, 3, "Zomato"];

const TIME_LABELS: [number, string][] = [[0, "09:00"], [4, "11:00"], [8, "14:00"]];

const HUE = { 1: "var(--tier-1)", 2: "var(--tier-2)", 3: "var(--tier-3)" } as const;

const y = (row: number) =>
  HEAD_H + row * ROW_H + (row > BREAK_AFTER ? BREAK_H : 0);

const W = GUTTER + ROOMS * COL_W;
const H = HEAD_H + ROWS * ROW_H + BREAK_H;

function Block({
  col, row, span, hue, tint, line, label, student, mark,
}: {
  col: number; row: number; span: number;
  hue: string; tint: string; line: string;
  label: string; student: string; mark?: string;
}) {
  const x = GUTTER + col * COL_W + 1;
  const top = y(row) + 1;
  const h = y(row + span - 1) + ROW_H - y(row) - 3;
  return (
    <g>
      <rect x={x} y={top} width={COL_W - 3} height={h} rx="2.5"
            fill={tint} stroke={line} strokeWidth="0.7" />
      <rect x={x} y={top} width="2.2" height={h} fill={hue} />
      {mark && (
        <text x={x + COL_W - 7} y={top + 7.4} fontSize="6" fontWeight="700"
              fill={hue} textAnchor="middle">{mark}</text>
      )}
      {/* The specimen renders at roughly 1.7x its viewBox, so 6px here lands
          around 10px on screen. */}
      <text x={x + 5} y={top + 7.6} fontSize="6" fontWeight="600"
            fill="var(--ink)" fontFamily="var(--ui)">{label}</text>
      {h > 16 && (
        <text x={x + 5} y={top + 15} fontSize="5.2"
              fill="var(--ink-3)" fontFamily="var(--mono)">{student}</text>
      )}
    </g>
  );
}

/** A fixed set, so the specimen renders the same on every load. */
const IDS = [
  "S0390", "S0203", "S0694", "S0274", "S0305", "S0601", "S0240", "S0188",
  "S0300", "S0447", "S0012", "S0592", "S0643", "S0386", "S0765", "S0351",
  "S0074", "S0616", "S0096", "S0784", "S0042", "S0536", "S0709", "S0545",
  "S0128", "S0675", "S0472", "S0191",
];

export default function BoardSpecimen() {
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label="A day of the schedule: six rooms across the top, time down the side, each interview a coloured block.">
      {/* room headers */}
      {Array.from({ length: ROOMS }, (_, c) => (
        <text key={c} x={GUTTER + c * COL_W + COL_W / 2} y={10}
              fontSize="7" fontWeight="600" fill="var(--ink-2)"
              textAnchor="middle" fontFamily="var(--ui)">
          R{c + 1}
        </text>
      ))}
      <line x1="0" y1={HEAD_H - 0.5} x2={W} y2={HEAD_H - 0.5} stroke="var(--line-strong)" strokeWidth="1" />
      <line x1={GUTTER - 0.5} y1="0" x2={GUTTER - 0.5} y2={H} stroke="var(--line-strong)" strokeWidth="1" />

      {/* hour rules and the times that name them */}
      {Array.from({ length: ROWS }, (_, r) => (
        <line key={r} x1={GUTTER} y1={y(r) + ROW_H - 0.5} x2={W} y2={y(r) + ROW_H - 0.5}
              stroke="var(--line)" strokeWidth="0.7" />
      ))}
      {TIME_LABELS.map(([row, label]) => (
        <text key={label} x={GUTTER - 6} y={y(row) + 10}
              fontSize="6.5" fill="var(--ink-3)" textAnchor="end" fontFamily="var(--mono)">
          {label}
        </text>
      ))}

      {/* lunch */}
      <rect x="0" y={y(BREAK_AFTER) + ROW_H} width={W} height={BREAK_H} fill="var(--sunken)" />
      <line x1="0" y1={y(BREAK_AFTER) + ROW_H} x2={W} y2={y(BREAK_AFTER) + ROW_H} stroke="var(--line-strong)" strokeWidth="0.7" />
      <line x1="0" y1={y(BREAK_AFTER + 1)} x2={W} y2={y(BREAK_AFTER + 1)} stroke="var(--line-strong)" strokeWidth="0.7" />
      <text x="5" y={y(BREAK_AFTER) + ROW_H + 6.3} fontSize="5.5" fontWeight="650"
            letterSpacing="0.08em" fill="var(--ink-3)" fontFamily="var(--ui)">
        LUNCH
      </text>

      {BLOCKS.map(([col, row, span, tier, label], i) => (
        <Block
          key={`${col}-${row}`}
          col={col} row={row} span={span}
          hue={HUE[tier]}
          tint={`color-mix(in srgb, ${HUE[tier]} 14%, var(--surface))`}
          line={`color-mix(in srgb, ${HUE[tier]} 30%, var(--surface))`}
          label={label}
          student={IDS[i % IDS.length]}
        />
      ))}

      <Block
        col={MOVED[0]} row={MOVED[1]} span={MOVED[2]}
        hue="var(--st-moved-mark)"
        tint="color-mix(in srgb, var(--st-moved-mark) 24%, var(--surface))"
        line="var(--st-moved)"
        label={MOVED[4]}
        student="S0558"
        mark="⇅"
      />
    </svg>
  );
}
