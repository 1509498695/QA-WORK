import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const SHEET_NAME = "测试用例";
const DATA_START_ROW = 11;
const RESERVED_END_ROW = 10010;
const CASE_HEADERS = [
  "用例编号",
  "一级模块",
  "二级模块",
  "检查点",
  "前置条件",
  "操作步骤",
  "预期结果",
  "优先级",
  "测试结果",
  "备注",
];
const RESULT_CODES = ["P", "F", "D", "N/A"];
const COLORS = {
  ink: "#20322C",
  forest: "#2F5D50",
  forestDark: "#24483F",
  sage: "#DCE8E1",
  cream: "#F4F1E8",
  sand: "#E8E0D0",
  white: "#FFFFFF",
  line: "#CBD5CF",
  muted: "#63716B",
  pass: "#DDF3E4",
  fail: "#F8D9D7",
  defer: "#FFF1C7",
  na: "#E6E8EB",
};


function parseArgs(argv) {
  const [command, ...rest] = argv;
  const options = { command };
  for (let index = 0; index < rest.length; index += 1) {
    const token = rest[index];
    if (!token.startsWith("--")) {
      throw new Error(`未知参数：${token}`);
    }
    const key = token.slice(2).replaceAll("-", "_");
    if (key === "overwrite") {
      options.overwrite = true;
      continue;
    }
    const value = rest[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${token} 缺少值`);
    }
    options[key] = value;
    index += 1;
  }
  return options;
}


function requireOption(options, name) {
  const value = options[name];
  if (!value) {
    throw new Error(`缺少 --${name.replaceAll("_", "-")}`);
  }
  return path.resolve(value);
}


function normalizeValue(value) {
  if (value === null || value === undefined) return "";
  return String(value).replaceAll("\r\n", "\n").replaceAll("\r", "\n");
}


function sha256Bytes(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}


async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}


async function writeJson(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await fs.rename(temporary, filePath);
}


function formatBaseSheet(sheet) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(10);

  sheet.getRange("A1:J1").merge();
  sheet.getRange("I2:J2").merge();
  sheet.getRange("B3:G3").merge();
  sheet.getRange("B4:G4").merge();
  sheet.getRange("B5:C5").merge();
  sheet.getRange("E5:G5").merge();
  sheet.getRange("A9:J9").merge();

  sheet.getRange("A1:J1").values = [["本地测试用例 - qa-case-xlsx"]];
  sheet.getRange("A2:J8").values = [
    ["需求名称", "", "用例总数", "", "项目", "", "交付模式", "测试结果类型", "结果统计", null],
    ["来源文件", "", null, null, null, null, null, "测试通过（P）", "", null],
    ["源包 SHA-256", "", null, null, null, null, null, "未通过（F）", "", null],
    ["生成状态", "", null, "规则版本", "", null, null, "删除用例（D）", "", null],
    ["", "", "", "", "", "", "", "无需测试（N/A）", "", null],
    ["", "", "", "", "", "", "", "执行率", "", null],
    ["", "", "", "", "", "", "", "通过率", "", null],
  ];
  sheet.getRange("A9:J9").values = [["用例明细（固定 A:J）"]];
  sheet.getRange("A10:J10").values = [CASE_HEADERS];

  sheet.getRange("D2").formulas = [[`=MAX(COUNTA($G$${DATA_START_ROW}:$G$${RESERVED_END_ROW}),COUNTA($F$${DATA_START_ROW}:$F$${RESERVED_END_ROW}))`]];
  sheet.getRange("I3:I6").formulas = RESULT_CODES.map(
    (code) => [`=COUNTIF($I$${DATA_START_ROW}:$I$${RESERVED_END_ROW},"${code}")`],
  );
  sheet.getRange("I7").formulas = [["=IF($D$2=0,0,SUM($I$3:$I$6)/$D$2)"]];
  sheet.getRange("I8").formulas = [["=IF($D$2=0,0,$I$3/$D$2)"]];

  sheet.getRange("A1:J1").format = {
    fill: COLORS.forestDark,
    font: { bold: true, color: COLORS.white, size: 18, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("A1:J1").format.rowHeight = 36;

  sheet.getRange("A2:J8").format = {
    font: { color: COLORS.ink, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A2:A5").format = {
    fill: COLORS.sage,
    font: { bold: true, color: COLORS.ink, name: "Microsoft YaHei" },
    verticalAlignment: "center",
  };
  sheet.getRange("C2:C2").format = {
    fill: COLORS.sage,
    font: { bold: true, color: COLORS.ink, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("E2:E2").format = {
    fill: COLORS.sage,
    font: { bold: true, color: COLORS.ink, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("G2:G2").format = {
    fill: COLORS.sage,
    font: { bold: true, color: COLORS.ink, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("D5:D5").format = {
    fill: COLORS.sage,
    font: { bold: true, color: COLORS.ink, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("H2:J8").format = {
    fill: COLORS.cream,
    font: { color: COLORS.ink, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    horizontalAlignment: "center",
  };
  sheet.getRange("H2:J2").format = {
    fill: COLORS.forest,
    font: { bold: true, color: COLORS.white, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("I7:I8").format.numberFormat = "0.0%";
  sheet.getRange("A2:J8").format.borders = {
    preset: "inside",
    style: "thin",
    color: COLORS.line,
  };
  sheet.getRange("A2:J8").format.borders = {
    preset: "all",
    style: "thin",
    color: COLORS.line,
  };

  sheet.getRange("A9:J9").format = {
    fill: COLORS.sand,
    font: { bold: true, color: COLORS.ink, size: 11, name: "Microsoft YaHei" },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  sheet.getRange("A9:J9").format.rowHeight = 26;
  sheet.getRange("A10:J10").format = {
    fill: COLORS.forest,
    font: { bold: true, color: COLORS.white, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.white },
  };
  sheet.getRange("A10:J10").format.rowHeight = 32;

  const widths = [70, 120, 120, 230, 220, 330, 330, 100, 85, 155];
  widths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidthPx = width;
  });
  sheet.getRange("A2:J8").format.rowHeight = 24;
  sheet.getRange("A3:A4").format.rowHeight = 32;
}


function configureEditableRows(sheet, endRow) {
  const effectiveEnd = Math.max(endRow, DATA_START_ROW);
  const allData = sheet.getRange(`A${DATA_START_ROW}:J${effectiveEnd}`);
  allData.format = {
    font: { color: COLORS.ink, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange(`A${DATA_START_ROW}:A${effectiveEnd}`).format.horizontalAlignment = "center";
  sheet.getRange(`H${DATA_START_ROW}:I${effectiveEnd}`).format.horizontalAlignment = "center";
  sheet.getRange(`B${DATA_START_ROW}:G${effectiveEnd}`).format.horizontalAlignment = "left";
  sheet.getRange(`J${DATA_START_ROW}:J${effectiveEnd}`).format.horizontalAlignment = "left";
  allData.format.autofitRows();

  sheet.getRange(`H${DATA_START_ROW}:H${RESERVED_END_ROW}`).dataValidation = {
    rule: { type: "list", values: ["P0", "P1", "P2"] },
  };
  sheet.getRange(`I${DATA_START_ROW}:I${RESERVED_END_ROW}`).dataValidation = {
    rule: { type: "list", values: RESULT_CODES },
  };

  const resultRange = sheet.getRange(`I${DATA_START_ROW}:I${RESERVED_END_ROW}`);
  resultRange.conditionalFormats.deleteAll();
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"P"',
    format: { fill: COLORS.pass, font: { color: COLORS.forestDark, bold: true } },
  });
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"F"',
    format: { fill: COLORS.fail, font: { color: "#8C2F2C", bold: true } },
  });
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"D"',
    format: { fill: COLORS.defer, font: { color: "#7A5A00", bold: true } },
  });
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"N/A"',
    format: { fill: COLORS.na, font: { color: COLORS.muted, bold: true } },
  });
}


function removeTables(sheet) {
  for (const table of [...sheet.tables.items]) {
    table.delete();
  }
}


function addCasesTable(sheet, endRow) {
  removeTables(sheet);
  const effectiveEnd = Math.max(endRow, DATA_START_ROW);
  const table = sheet.tables.add(`A10:J${effectiveEnd}`, true, "TestCasesTable");
  table.style = "TableStyleMedium4";
  table.showBandedColumns = false;
  table.showTotals = false;
  table.showFilterButton = true;
}


function createTemplateWorkbook() {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(SHEET_NAME);
  formatBaseSheet(sheet);
  sheet.getRange("A11:J11").values = [[null, null, null, null, null, null, null, null, null, null]];
  configureEditableRows(sheet, DATA_START_ROW);
  addCasesTable(sheet, DATA_START_ROW);
  return workbook;
}


async function renderWorkbook(workbook, outputPath, endRow) {
  const preview = await workbook.render({
    sheetName: SHEET_NAME,
    range: `A1:J${Math.max(Math.min(endRow, 80), 14)}`,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(outputPath, new Uint8Array(await preview.arrayBuffer()));
}


async function exportWorkbook(workbook, outputPath, overwrite) {
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  if (!overwrite) {
    try {
      await fs.access(outputPath);
      throw new Error(`输出文件已存在；如需覆盖请增加 --overwrite：${outputPath}`);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
  await fs.rm(`${outputPath}.inspect.ndjson`, { force: true });
}


async function createTemplate(options) {
  const outputPath = requireOption(options, "out");
  const previewPath = options.preview ? path.resolve(options.preview) : null;
  const workbook = createTemplateWorkbook();
  if (previewPath) {
    await fs.mkdir(path.dirname(previewPath), { recursive: true });
    await renderWorkbook(workbook, previewPath, 14);
  }
  await exportWorkbook(workbook, outputPath, Boolean(options.overwrite));
  return { status: "ok", output: outputPath };
}


async function createSourceFixture(options) {
  const outputPath = requireOption(options, "out");
  const previewPath = options.preview ? path.resolve(options.preview) : null;
  const imagePath = options.image ? path.resolve(options.image) : null;
  const workbook = Workbook.create();
  const requirement = workbook.worksheets.add("需求");
  const review = workbook.worksheets.add("CP反馈");

  requirement.showGridLines = false;
  requirement.getRange("A1:D5").values = [
    ["功能", "周年庆宝箱", "项目", "SAMO"],
    ["开放条件", "活动开启且玩家达到 10 级", "奖励", "首次开启获得 100 金币"],
    ["重复开启", "当天仅首次获得奖励", "时间", "每日 00:00 刷新"],
    ["异常", "背包已满时奖励发送到邮件", "链接", "https://example.invalid/not-followed"],
    ["说明", "以本 Sheet 为正式策划内容", "", ""],
  ];
  requirement.getRange("A1:D1").format = {
    fill: COLORS.forest,
    font: { bold: true, color: COLORS.white, name: "Microsoft YaHei" },
  };
  requirement.getRange("A1:D5").format.wrapText = true;
  requirement.getRange("A1:D5").format.borders = { preset: "all", style: "thin", color: COLORS.line };
  requirement.getRange("A1:D5").format.autofitColumns();
  requirement.getRange("A1:D5").format.autofitRows();
  if (imagePath) {
    const imageBytes = await fs.readFile(imagePath);
    const suffix = path.extname(imagePath).toLowerCase() === ".jpg" ? "jpeg" : "png";
    requirement.images.add({
      dataUrl: `data:image/${suffix};base64,${imageBytes.toString("base64")}`,
      anchor: { from: { row: 6, col: 0 }, extent: { widthPx: 240, heightPx: 100 } },
    });
  }

  review.getRange("A1:B2").values = [
    ["反馈项", "此 Sheet 默认排除"],
    ["历史意见", "不应进入 source_packet.content_units"],
  ];
  review.getRange("A1:B2").format = {
    fill: COLORS.defer,
    font: { color: COLORS.ink, name: "Microsoft YaHei" },
    wrapText: true,
  };
  review.getRange("A1:B2").format.autofitColumns();

  if (previewPath) {
    await fs.mkdir(path.dirname(previewPath), { recursive: true });
    const preview = await workbook.render({ sheetName: "需求", range: "A1:D12", scale: 1, format: "png" });
    await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  }
  await exportWorkbook(workbook, outputPath, Boolean(options.overwrite));
  return { status: "ok", output: outputPath };
}


function assertFinalCases(finalCases, readiness) {
  if (readiness.status !== "ok" || !["formal", "draft"].includes(readiness.delivery_mode)) {
    throw new Error("delivery_readiness 未通过，禁止生成工作簿");
  }
  if (!Array.isArray(finalCases.cases) || finalCases.cases.length === 0) {
    throw new Error("final_cases.cases 必须为非空数组");
  }
  for (const [index, testCase] of finalCases.cases.entries()) {
    for (const field of CASE_HEADERS) {
      if (!(field in testCase)) throw new Error(`final_cases.cases[${index}] 缺少 ${field}`);
    }
    if (String(testCase["用例编号"]) !== String(index + 1)) {
      throw new Error(`final_cases.cases[${index}].用例编号 不连续`);
    }
    for (const field of CASE_HEADERS) {
      if (/https?:\/\//i.test(normalizeValue(testCase[field]))) {
        throw new Error(`final_cases.cases[${index}].${field} 含外部 URL，禁止写入正式用例表`);
      }
    }
  }
}


function casesMatrix(cases) {
  return cases.map((testCase) => CASE_HEADERS.map((field) => normalizeValue(testCase[field])));
}


function sourceNames(sourcePacket) {
  return (sourcePacket.files || []).map((item) => item.file_name).join("；");
}


function setDynamicFormulas(sheet, endRow) {
  sheet.getRange("D2").formulas = [[`=MAX(COUNTA($G$${DATA_START_ROW}:$G$${endRow}),COUNTA($F$${DATA_START_ROW}:$F$${endRow}))`]];
  sheet.getRange("I3:I6").formulas = RESULT_CODES.map(
    (code) => [`=COUNTIF($I$${DATA_START_ROW}:$I$${endRow},"${code}")`],
  );
  sheet.getRange("I7").formulas = [["=IF($D$2=0,0,SUM($I$3:$I$6)/$D$2)"]];
  sheet.getRange("I8").formulas = [["=IF($D$2=0,0,$I$3/$D$2)"]];
}


function populateWorkbook(workbook, finalCases, readiness, sourcePacket) {
  const sheet = workbook.worksheets.getItem(SHEET_NAME);
  removeTables(sheet);
  sheet.getRange(`A${DATA_START_ROW}:J${RESERVED_END_ROW}`).clear({ applyTo: "contents" });

  const cases = finalCases.cases;
  const endRow = DATA_START_ROW + cases.length - 1;
  sheet.getRange("A1:J1").values = [[
    `${readiness.requirement_name}测试用例 - qa-case-xlsx`,
  ]];
  sheet.getRange("B2").values = [[readiness.requirement_name]];
  sheet.getRange("F2").values = [[readiness.project_code || "generic"]];
  sheet.getRange("G2").values = [[readiness.delivery_mode === "formal" ? "正式" : "待确认草稿"]];
  sheet.getRange("B3:G3").values = [[sourceNames(sourcePacket)]];
  sheet.getRange("B4:G4").values = [[sourcePacket.package_sha256 || ""]];
  sheet.getRange("B5:C5").values = [[readiness.pending_count === 0 ? "全部门禁通过" : `待确认 ${readiness.pending_count} 项`]];
  sheet.getRange("E5:G5").values = [[readiness.rule_release_version || ""]];
  sheet.getRange(`A${DATA_START_ROW}:J${endRow}`).values = casesMatrix(cases);
  setDynamicFormulas(sheet, endRow);
  configureEditableRows(sheet, endRow);
  addCasesTable(sheet, endRow);
  return { sheet, endRow };
}


function normalizeMatrix(matrix) {
  return matrix.map((row) => row.map(normalizeValue));
}


function compareCases(actual, expected) {
  const errors = [];
  if (actual.length !== expected.length) {
    errors.push(`用例行数不一致：expected=${expected.length} actual=${actual.length}`);
  }
  const rows = Math.min(actual.length, expected.length);
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < CASE_HEADERS.length; column += 1) {
      if (actual[row][column] !== expected[row][column]) {
        errors.push(
          `A:J 回读不一致：row=${row + DATA_START_ROW} field=${CASE_HEADERS[column]} expected=${JSON.stringify(expected[row][column])} actual=${JSON.stringify(actual[row][column])}`,
        );
      }
    }
  }
  return errors;
}


async function verifyExportedWorkbook(outputPath, finalCases, readiness) {
  const input = await FileBlob.load(outputPath);
  const workbook = await SpreadsheetFile.importXlsx(input);
  const sheet = workbook.worksheets.getItem(SHEET_NAME);
  const endRow = DATA_START_ROW + finalCases.cases.length - 1;
  const expected = casesMatrix(finalCases.cases);
  const actual = normalizeMatrix(sheet.getRange(`A${DATA_START_ROW}:J${endRow}`).values);
  const errors = compareCases(actual, expected);

  const header = normalizeMatrix(sheet.getRange("A10:J10").values)[0];
  if (JSON.stringify(header) !== JSON.stringify(CASE_HEADERS)) {
    errors.push(`A:J 表头顺序不一致：${JSON.stringify(header)}`);
  }
  const title = normalizeValue(sheet.getRange("A1").values[0][0]);
  if (!title.includes(readiness.requirement_name)) errors.push("标题未包含需求名称");
  const formulas = normalizeMatrix(sheet.getRange("D2:I8").formulas);
  const formulaText = formulas.flat().join("\n").toUpperCase().replaceAll("$", "");
  for (const expectedFormulaToken of ["COUNTA", "COUNTIF", "SUM(I3:I6)", "D2"]) {
    if (!formulaText.includes(expectedFormulaToken)) {
      errors.push(`统计公式缺少 ${expectedFormulaToken}`);
    }
  }

  const formulaErrors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  const formulaErrorText = String(formulaErrors.ndjson || "");
  if (/"kind":"match"/.test(formulaErrorText) && !/"count":0/.test(formulaErrorText)) {
    errors.push("工作簿存在公式错误匹配，请检查 formula_error_scan")
  }

  const tableInspect = await workbook.inspect({
    kind: "table",
    range: `${SHEET_NAME}!A1:J${endRow}`,
    include: "values,formulas",
    tableMaxRows: Math.min(endRow, 40),
    tableMaxCols: 10,
    maxChars: 16000,
  });
  const bytes = await fs.readFile(outputPath);
  return {
    schema_version: "1.0",
    status: errors.length ? "invalid" : "ok",
    workbook_path: outputPath,
    workbook_sha256: sha256Bytes(bytes),
    sheet_name: SHEET_NAME,
    output_filename: path.basename(outputPath),
    expected_output_filename: readiness.output_filename,
    case_count: finalCases.cases.length,
    data_range: `A${DATA_START_ROW}:J${endRow}`,
    header_order: header,
    values_match: errors.every((item) => !item.startsWith("A:J 回读不一致") && !item.startsWith("用例行数")),
    formulas_present: !errors.some((item) => item.startsWith("统计公式缺少")),
    external_links_present: actual.flat().some((value) => /https?:\/\//i.test(value)),
    errors,
    formula_error_scan: formulaErrorText,
    compact_inspect: tableInspect.ndjson,
  };
}


async function buildWorkbook(options) {
  const templatePath = requireOption(options, "template");
  const runDir = requireOption(options, "run_dir");
  const outputDir = requireOption(options, "output_dir");
  const readiness = await readJson(path.join(runDir, "delivery_readiness.json"));
  const finalCases = await readJson(path.join(runDir, "final_cases.json"));
  const sourcePacket = await readJson(path.join(runDir, "source_packet.json"));
  assertFinalCases(finalCases, readiness);
  if (path.basename(readiness.output_filename) !== readiness.output_filename) {
    throw new Error("delivery_readiness.output_filename 不是安全文件名");
  }

  const input = await FileBlob.load(templatePath);
  const workbook = await SpreadsheetFile.importXlsx(input);
  const { endRow } = populateWorkbook(workbook, finalCases, readiness, sourcePacket);
  const outputPath = path.join(outputDir, readiness.output_filename);
  const previewPath = path.join(runDir, "workbook-preview.png");
  await renderWorkbook(workbook, previewPath, endRow);
  await exportWorkbook(workbook, outputPath, Boolean(options.overwrite));
  const readback = await verifyExportedWorkbook(outputPath, finalCases, readiness);
  if (readback.external_links_present) {
    readback.status = "invalid";
    readback.errors.push("工作簿 A:J 中存在外部 URL");
  }
  await writeJson(path.join(runDir, "workbook_readback.json"), readback);
  if (readback.status !== "ok") {
    throw new Error(`工作簿精确回读失败：${readback.errors.join("；")}`);
  }
  return {
    status: "ok",
    output: outputPath,
    preview: previewPath,
    readback: path.join(runDir, "workbook_readback.json"),
    case_count: finalCases.cases.length,
  };
}


async function main() {
  const options = parseArgs(process.argv.slice(2));
  let result;
  if (options.command === "create-template") {
    result = await createTemplate(options);
  } else if (options.command === "create-source-fixture") {
    result = await createSourceFixture(options);
  } else if (options.command === "build") {
    result = await buildWorkbook(options);
  } else {
    throw new Error("命令必须是 create-template、create-source-fixture 或 build");
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}


main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ status: "invalid", error: String(error?.stack || error) }, null, 2)}\n`);
  process.exitCode = 1;
});
