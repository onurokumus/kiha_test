import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = path.join(repoRoot, "sample_csv");

function mulberry32(seed) {
  return () => {
    let value = (seed += 0x6d2b79f5);
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function noise(random, amplitude) {
  return (random() + random() + random() + random() - 2) * amplitude;
}

function fixed(value, digits) {
  return Number.isFinite(value) ? value.toFixed(digits) : String(value);
}

function csvField(value, separator) {
  const text = String(value);
  return text.includes(separator) || /["\r\n]/.test(text)
    ? `"${text.replaceAll('"', '""')}"`
    : text;
}

async function writeCsv(filename, headers, rows, separator = ",") {
  const lines = [headers, ...rows].map((row) =>
    row.map((value) => csvField(value, separator)).join(separator),
  );
  await fs.writeFile(path.join(outputDir, filename), `${lines.join("\n")}\n`, "utf8");
}

const demoHeaders = [
  "time_s",
  "Test_ID",
  "command_pct",
  "rpm",
  "thrust_n",
  "torque_nm",
  "shaft_power_w",
  "voltage_v",
  "current_a",
  "electrical_power_w",
  "motor_temp_c",
  "esc_temp_c",
  "vibration_x_g",
  "vibration_y_g",
  "vibration_z_g",
  "airflow_mps",
  "ambient_temp_c",
];

const demoStages = [
  { start: 2, end: 8, id: 1, command: 25 },
  { start: 9, end: 15, id: 2, command: 45 },
  { start: 16, end: 22, id: 3, command: 65 },
  { start: 23, end: 29, id: 4, command: 85 },
];

function propellerRows({
  fsHz,
  durationS,
  stages,
  seed,
  rpmScale = 1,
  thrustScale = 1,
  torqueScale = 1,
  vibrationScale = 1,
  temperatureBias = 0,
  timeOffset = () => 0,
  mutateRow,
  includeNote = false,
}) {
  const random = mulberry32(seed);
  const rows = [];
  const dt = 1 / fsHz;
  const ambient = 22 + temperatureBias;
  let rpmState = 0;
  let motorTemp = ambient;
  let escTemp = ambient;

  for (let i = 0; i < Math.round(fsHz * durationS); i += 1) {
    const signalTime = i * dt;
    const stage = stages.find(({ start, end }) => signalTime >= start && signalTime < end);
    const testId = stage?.id ?? 0;
    const command = stage?.command ?? 0;
    const targetRpm = stage ? (450 + command * 54) * rpmScale : 0;
    const tau = stage ? 0.42 : 0.7;
    rpmState += (targetRpm - rpmState) * (1 - Math.exp(-dt / tau));
    const rpm = Math.max(0, rpmState + noise(random, 5 + rpmState * 0.0012));

    const thrust = Math.max(
      0,
      thrustScale * 0.00000105 * rpm ** 2 + noise(random, 0.08 + rpm * 0.000006),
    );
    const torque = Math.max(
      0,
      torqueScale * 0.000000045 * rpm ** 2 + noise(random, 0.008 + rpm * 0.000001),
    );
    const current = Math.max(
      0.8,
      1.15 + 0.00095 * rpm + 0.00000036 * rpm ** 2 + noise(random, 0.12),
    );
    const voltage = 50.4 - 0.082 * current + noise(random, 0.025);
    const shaftPower = torque * rpm * (2 * Math.PI / 60);
    const electricalPower = voltage * current;

    const motorTarget = ambient + current * 1.22;
    const escTarget = ambient + current * 0.78;
    motorTemp += (motorTarget - motorTemp) * (1 - Math.exp(-dt / 7.5));
    escTemp += (escTarget - escTemp) * (1 - Math.exp(-dt / 5.5));

    const oneP = rpm / 60;
    const vibrationBase = 0.005 + vibrationScale * rpm * 0.000011;
    const vibrationX =
      vibrationBase * Math.sin(2 * Math.PI * oneP * signalTime + 0.25) +
      0.35 * vibrationBase * Math.sin(2 * Math.PI * 2 * oneP * signalTime) +
      noise(random, 0.003);
    const vibrationY =
      0.82 * vibrationBase * Math.sin(2 * Math.PI * oneP * signalTime + 1.1) +
      0.28 * vibrationBase * Math.sin(2 * Math.PI * 3 * oneP * signalTime) +
      noise(random, 0.003);
    const vibrationZ =
      0.58 * vibrationBase * Math.sin(2 * Math.PI * 2 * oneP * signalTime + 0.6) +
      noise(random, 0.0025);
    const airflow = Math.max(0, rpm * 0.00435 + noise(random, 0.12));

    const row = [
      fixed(signalTime + timeOffset(i, signalTime), 6),
      String(testId),
      String(command),
      fixed(rpm, 2),
      fixed(thrust, 4),
      fixed(torque, 5),
      fixed(shaftPower, 2),
      fixed(voltage, 3),
      fixed(current, 3),
      fixed(electricalPower, 2),
      fixed(motorTemp, 3),
      fixed(escTemp, 3),
      fixed(vibrationX, 6),
      fixed(vibrationY, 6),
      fixed(vibrationZ, 6),
      fixed(airflow, 3),
      fixed(ambient + 0.2 * Math.sin(signalTime / 12), 3),
    ];

    if (includeNote) {
      row.push(stage ? `steady_run_${stage.id}` : "idle_or_transition");
    }
    mutateRow?.(row, i, signalTime);
    rows.push(row);
  }
  return rows;
}

function decimalComma(value, digits) {
  return fixed(value, digits).replace(".", ",");
}

function clockTime(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds
    .toFixed(1)
    .padStart(4, "0")}`.replace(".", ",");
}

function kihaDialectRows() {
  const fsHz = 100;
  const random = mulberry32(260716);
  const rows = [];
  const startClockS = 12 * 60 + 34.5;
  const stages = [
    { start: 2, end: 6, id: 1, rpm: 1800 },
    { start: 7, end: 11, id: 2, rpm: 3200 },
    { start: 12, end: 16, id: 3, rpm: 4600 },
  ];
  let rpmState = 0;
  let motorTemp = 31;
  let escTemp = 30;

  for (let i = 0; i < fsHz * 16; i += 1) {
    const t = i / fsHz;
    const stage = stages.find(({ start, end }) => t >= start && t < end);
    const targetRpm = stage?.rpm ?? 0;
    rpmState += (targetRpm - rpmState) * (1 - Math.exp(-1 / fsHz / 0.4));
    const rpm = Math.max(0, rpmState + noise(random, 4));
    const current = 1.1 + 0.0011 * rpm + 0.00000035 * rpm ** 2 + noise(random, 0.1);
    const voltage = 50.1 - current * 0.075 + noise(random, 0.02);
    const thrust = Math.max(0, 0.00000102 * rpm ** 2 + noise(random, 0.08));
    const torque = Math.max(0, 0.000000044 * rpm ** 2 + noise(random, 0.008));
    motorTemp += (31 + current * 1.1 - motorTemp) * 0.0017;
    escTemp += (30 + current * 0.72 - escTemp) * 0.0022;
    const vibration =
      (0.004 + rpm * 0.00001) * Math.sin(2 * Math.PI * (rpm / 60) * t) +
      noise(random, 0.0025);

    rows.push([
      clockTime(Math.round((startClockS + t) * 10) / 10),
      decimalComma(voltage, 4),
      decimalComma(current, 4),
      String(Math.round(rpm)),
      decimalComma(motorTemp, 3),
      decimalComma(escTemp, 3),
      decimalComma(thrust, 5),
      decimalComma(torque, 6),
      decimalComma(vibration, 6),
      decimalComma(0.83 * vibration + noise(random, 0.002), 6),
      String(stage?.id ?? 0),
      stage ? `run_${stage.id}` : "idle",
    ]);
  }
  return rows;
}

function generatedTimeRows() {
  const fsHz = 2048;
  const random = mulberry32(2048);
  const rows = [];
  let rpmState = 0;

  for (let i = 0; i < 8192; i += 1) {
    const t = i / fsHz;
    const runId = i >= 512 && i < 3328 ? 1 : i >= 3840 && i < 7168 ? 2 : 0;
    const command = runId === 1 ? 40 : runId === 2 ? 75 : 0;
    const targetRpm = runId === 1 ? 2400 : runId === 2 ? 4400 : 0;
    rpmState += (targetRpm - rpmState) * (1 - Math.exp(-1 / fsHz / 0.25));
    const rpm = Math.max(0, rpmState + noise(random, 4));
    const thrust = Math.max(0, 0.00000103 * rpm ** 2 + noise(random, 0.06));
    const torque = Math.max(0, 0.000000045 * rpm ** 2 + noise(random, 0.006));
    const vibration =
      (0.004 + rpm * 0.000012) * Math.sin(2 * Math.PI * (rpm / 60) * t) +
      noise(random, 0.002);
    rows.push([
      "not-recorded",
      String(runId),
      String(command),
      fixed(rpm, 2),
      fixed(thrust, 4),
      fixed(torque, 5),
      fixed(vibration, 6),
    ]);
  }
  return rows;
}

await fs.mkdir(outputDir, { recursive: true });

await writeCsv(
  "ptt_demo_run_a.csv",
  demoHeaders,
  propellerRows({
    fsHz: 256,
    durationS: 32,
    stages: demoStages,
    seed: 1001,
  }),
);

await writeCsv(
  "ptt_demo_run_b.csv",
  demoHeaders,
  propellerRows({
    fsHz: 256,
    durationS: 32,
    stages: demoStages,
    seed: 2002,
    rpmScale: 0.985,
    thrustScale: 0.94,
    torqueScale: 1.06,
    vibrationScale: 1.35,
    temperatureBias: 1.4,
  }),
);

const gapHeaders = [...demoHeaders, "operator_note"];
const gapIndex = Object.fromEntries(gapHeaders.map((name, index) => [name, index]));
const gapStages = [
  { start: 1.5, end: 7, id: 1, command: 30 },
  { start: 8, end: 14, id: 2, command: 55 },
  { start: 15, end: 22.5, id: 3, command: 80 },
];
const gapRows = propellerRows({
  fsHz: 200,
  durationS: 24,
  stages: gapStages,
  seed: 3003,
  timeOffset: (i) => (i >= 2400 ? 0.025 : 0),
  includeNote: true,
  mutateRow: (row, i) => {
    if (i >= 900 && i < 980) row[gapIndex.thrust_n] = "";
    if (i >= 2250 && i < 2330) row[gapIndex.torque_nm] = "NaN";
    if (i >= 3400 && i < 3440) row[gapIndex.current_a] = "null";
    if (i === 1800) row[gapIndex.vibration_z_g] = "inf";
    if (i === 3601) row[gapIndex.motor_temp_c] = "None";
  },
});
await writeCsv("ptt_nan_gaps.csv", gapHeaders, gapRows);

await writeCsv(
  "ptt_kiha_dialect.csv",
  [
    "TIME",
    "Battery_Volt_L",
    "Battery_Current_L",
    "RPM_L",
    "Motor_Temp_L",
    "ESC_Temp_Main_L",
    "LHM_Fx",
    "LHM_Mz",
    "LHM_VIBO1x",
    "LHM_VIBO1y",
    "Test_ID",
    "Note_Text",
  ],
  kihaDialectRows(),
  ";",
);

await writeCsv(
  "ptt_generated_time.csv",
  ["TIME", "Run_ID", "command_pct", "rpm", "thrust_n", "torque_nm", "vibration_g"],
  generatedTimeRows(),
);

await writeCsv(
  "ptt_invalid_single_row.csv",
  ["time_s", "Test_ID", "rpm", "thrust_n"],
  [["0.0", "1", "1800", "12.4"]],
);

console.log(`Generated sample CSV files in ${outputDir}`);
