import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import JSZip from "jszip";


const SHEET_NAME = "测试用例";
const DATA_START_ROW = 11;
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
const DISPLAY_HEADERS = [
  "用例编号",
  "一级模块",
  "二级模块",
  "检查点（必要项）",
  "前置条件",
  "操作步骤（必要项）",
  "预期结果（必要项）",
  "优先级",
  "测试结果",
  "备注",
];
const RESULT_CODES = ["P", "F", "D", "N/A"];
const COLORS = {
  ink: "#222222",
  blue: "#86D3E5",
  yellow: "#FFF2CC",
  forest: "#2F5D50",
  white: "#FFFFFF",
  line: "#BFBFBF",
  muted: "#666666",
  pass: "#D9EAD3",
  fail: "#F4CCCC",
  defer: "#FFE599",
  na: "#D9D9D9",
};
const TOP_MERGES = [
  "A1:J1",
  "C2:C5",
  "D2:G5",
  "C6:C8",
  "D6:G8",
  "I2:J2",
  "I3:J3",
  "I4:J4",
  "I5:J5",
  "I6:J6",
  "I7:J7",
  "I8:J8",
  "A9:A10",
  "B9:D9",
  "E9:E10",
  "F9:F10",
  "G9:G10",
  "H9:H10",
  "I9:I10",
  "J9:J10",
];


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

  for (const address of TOP_MERGES) sheet.getRange(address).merge();

  sheet.getRange("A1").values = [["测试用例"]];
  sheet.getRange("A2:A8").values = [
    ["DRI"],
    ["客户端"],
    ["服务器"],
    ["UT/UE"],
    ["系统/运营/战斗策划"],
    ["文案/叙事"],
    ["数值"],
  ];
  sheet.getRange("B2:B8").values = [[""], [""], [""], [""], [""], [""], [""]];
  sheet.getRange("C2").values = [["用例总数"]];
  sheet.getRange("C6").values = [["SVN路径或者参考文档"]];
  sheet.getRange("H2:H8").values = [
    ["测试结果类型"],
    ["测试通过（P）"],
    ["未通过（F）"],
    ["删除用例（D）"],
    ["无需测试（N/A）"],
    ["执行率"],
    ["通过率"],
  ];
  sheet.getRange("I2").values = [["结果统计"]];

  sheet.getRange("A9").values = [["用例编号"]];
  sheet.getRange("B9").values = [["测试标题"]];
  sheet.getRange("B10:D10").values = [["一级模块", "二级模块", "检查点（必要项）"]];
  sheet.getRange("E9").values = [["前置条件"]];
  sheet.getRange("F9").values = [["操作步骤（必要项）"]];
  sheet.getRange("G9").values = [["预期结果（必要项）"]];
  sheet.getRange("H9").values = [["优先级"]];
  sheet.getRange("I9").values = [["测试结果"]];
  sheet.getRange("J9").values = [["备注"]];

  sheet.getRange("D2").formulas = [[`=COUNTA($A$${DATA_START_ROW}:$A$${DATA_START_ROW})`]];
  sheet.getRange("I3:I6").formulas = RESULT_CODES.map(
    (code) => [`=COUNTIF($I$${DATA_START_ROW}:$I$${DATA_START_ROW},"${code}")`],
  );
  sheet.getRange("I7").formulas = [["=IF($D$2=0,0,SUM($I$3:$I$6)/$D$2)"]];
  sheet.getRange("I8").formulas = [["=IF($D$2=0,0,$I$3/$D$2)"]];

  sheet.getRange("A1:J1").format = {
    fill: COLORS.blue,
    font: { bold: true, color: COLORS.ink, size: 14, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange("A1:J1").format.rowHeight = 34;

  sheet.getRange("A2:J8").format = {
    font: { color: COLORS.ink, size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange("A2:A8").format.fill = COLORS.blue;
  sheet.getRange("A2:A8").format.font = {
    bold: true,
    color: COLORS.ink,
    size: 10,
    name: "Microsoft YaHei",
  };
  sheet.getRange("B2:B8").format.fill = COLORS.yellow;
  sheet.getRange("C2:C8").format.fill = COLORS.blue;
  sheet.getRange("C2:C8").format.font = {
    bold: true,
    color: COLORS.ink,
    size: 10,
    name: "Microsoft YaHei",
  };
  sheet.getRange("D2:G8").format.fill = COLORS.white;
  sheet.getRange("D2:G8").format.horizontalAlignment = "left";
  sheet.getRange("D2").format = {
    fill: COLORS.white,
    font: { bold: true, color: COLORS.ink, size: 16, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("H2:H8").format.fill = COLORS.blue;
  sheet.getRange("H2:H8").format.font = {
    bold: true,
    color: COLORS.ink,
    size: 10,
    name: "Microsoft YaHei",
  };
  sheet.getRange("I2:J8").format.fill = COLORS.white;
  sheet.getRange("I2:J8").format.horizontalAlignment = "center";
  sheet.getRange("I2").format = {
    fill: COLORS.blue,
    font: { bold: true, color: COLORS.ink, size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.getRange("I7:I8").format.numberFormat = "0.0%";
  sheet.getRange("A2:J8").format.rowHeight = 25;
  sheet.getRange("A6:J6").format.rowHeight = 32;

  sheet.getRange("A9:J10").format = {
    fill: COLORS.blue,
    font: { bold: true, color: COLORS.ink, size: 10, name: "Microsoft YaHei" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange("A9:J9").format.rowHeight = 28;
  sheet.getRange("A10:J10").format.rowHeight = 28;

  const widths = [110, 138, 136, 188, 250, 342, 292, 108, 108, 126];
  widths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidthPx = width;
  });
}


function configureEditableRows(sheet, endRow) {
  const effectiveEnd = Math.max(endRow, DATA_START_ROW);
  const allData = sheet.getRange(`A${DATA_START_ROW}:J${effectiveEnd}`);
  allData.format = {
    fill: COLORS.white,
    font: { color: COLORS.ink, size: 10, name: "Microsoft YaHei" },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
  sheet.getRange(`A${DATA_START_ROW}:A${effectiveEnd}`).format.horizontalAlignment = "center";
  sheet.getRange(`B${DATA_START_ROW}:G${effectiveEnd}`).format.horizontalAlignment = "left";
  sheet.getRange(`H${DATA_START_ROW}:I${effectiveEnd}`).format.horizontalAlignment = "center";
  sheet.getRange(`J${DATA_START_ROW}:J${effectiveEnd}`).format.horizontalAlignment = "left";
  allData.format.autofitRows();

  sheet.getRange(`H${DATA_START_ROW}:H${effectiveEnd}`).dataValidation = {
    rule: { type: "list", values: ["P0", "P1", "P2"] },
  };
  sheet.getRange(`I${DATA_START_ROW}:I${effectiveEnd}`).dataValidation = {
    rule: { type: "list", values: RESULT_CODES },
  };

  const resultRange = sheet.getRange(`I${DATA_START_ROW}:I${effectiveEnd}`);
  resultRange.conditionalFormats.deleteAll();
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"P"',
    format: { fill: COLORS.pass, font: { color: "#274E13", bold: true } },
  });
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"F"',
    format: { fill: COLORS.fail, font: { color: "#990000", bold: true } },
  });
  resultRange.conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"D"',
    format: { fill: COLORS.defer, font: { color: "#7F6000", bold: true } },
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


function createTemplateWorkbook() {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add(SHEET_NAME);
  formatBaseSheet(sheet);
  sheet.getRange("A11:J11").values = [[null, null, null, null, null, null, null, null, null, null]];
  configureEditableRows(sheet, DATA_START_ROW);
  return workbook;
}


async function renderWorkbook(workbook, outputPath, endRow) {
  const headerPreview = await workbook.render({
    sheetName: SHEET_NAME,
    range: "A1:J10",
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(path.dirname(outputPath), "workbook-preview-header.png"),
    new Uint8Array(await headerPreview.arrayBuffer()),
  );
  const preview = await workbook.render({
    sheetName: SHEET_NAME,
    // Render the body separately. The header preview above carries both title rows.
    range: `A${DATA_START_ROW}:J${Math.max(Math.min(endRow, DATA_START_ROW + 32), 14)}`,
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


function contiguousGroups(cases, field, column, parentField = null) {
  const groups = [];
  let startIndex = 0;
  while (startIndex < cases.length) {
    const value = normalizeValue(cases[startIndex][field]);
    const parent = parentField ? normalizeValue(cases[startIndex][parentField]) : "";
    let endIndex = startIndex;
    while (
      endIndex + 1 < cases.length
      && normalizeValue(cases[endIndex + 1][field]) === value
      && (!parentField || normalizeValue(cases[endIndex + 1][parentField]) === parent)
    ) {
      endIndex += 1;
    }
    if (value && endIndex > startIndex) {
      groups.push({
        field,
        value,
        start_row: DATA_START_ROW + startIndex,
        end_row: DATA_START_ROW + endIndex,
        range: `${column}${DATA_START_ROW + startIndex}:${column}${DATA_START_ROW + endIndex}`,
      });
    }
    startIndex = endIndex + 1;
  }
  return groups;
}


function moduleMergeGroups(cases) {
  return [
    ...contiguousGroups(cases, "一级模块", "B"),
    ...contiguousGroups(cases, "二级模块", "C", "一级模块"),
  ];
}


function mergeModuleCells(sheet, cases) {
  for (const group of moduleMergeGroups(cases)) {
    sheet.getRange(group.range).merge();
  }
}


function setDynamicFormulas(sheet, endRow) {
  sheet.getRange("D2").formulas = [[`=COUNTA($A$${DATA_START_ROW}:$A$${endRow})`]];
  sheet.getRange("I3:I6").formulas = RESULT_CODES.map(
    (code) => [`=COUNTIF($I$${DATA_START_ROW}:$I$${endRow},"${code}")`],
  );
  sheet.getRange("I7").formulas = [["=IF($D$2=0,0,SUM($I$3:$I$6)/$D$2)"]];
  sheet.getRange("I8").formulas = [["=IF($D$2=0,0,$I$3/$D$2)"]];
}


function assertTemplateContract(workbook) {
  const sheet = workbook.worksheets.getItem(SHEET_NAME);
  const actual = [
    normalizeValue(sheet.getRange("A2").values[0][0]),
    normalizeValue(sheet.getRange("C2").values[0][0]),
    normalizeValue(sheet.getRange("C6").values[0][0]),
    normalizeValue(sheet.getRange("H2").values[0][0]),
    normalizeValue(sheet.getRange("I2").values[0][0]),
    normalizeValue(sheet.getRange("A9").values[0][0]),
    normalizeValue(sheet.getRange("B9").values[0][0]),
    normalizeValue(sheet.getRange("B10").values[0][0]),
    normalizeValue(sheet.getRange("C10").values[0][0]),
    normalizeValue(sheet.getRange("D10").values[0][0]),
  ];
  const expected = [
    "DRI",
    "用例总数",
    "SVN路径或者参考文档",
    "测试结果类型",
    "结果统计",
    "用例编号",
    "测试标题",
    "一级模块",
    "二级模块",
    "检查点（必要项）",
  ];
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`本地模板不符合个人用例版式：${JSON.stringify(actual)}`);
  }
  return sheet;
}


function populateWorkbook(workbook, finalCases, readiness, sourcePacket) {
  const sheet = assertTemplateContract(workbook);
  removeTables(sheet);

  const cases = finalCases.cases;
  const endRow = DATA_START_ROW + cases.length - 1;
  sheet.getRange("A1").values = [[`${readiness.requirement_name}测试用例`]];
  sheet.getRange("D6").values = [[sourceNames(sourcePacket)]];
  sheet.getRange(`A${DATA_START_ROW}:J${endRow}`).values = casesMatrix(cases);
  setDynamicFormulas(sheet, endRow);
  configureEditableRows(sheet, endRow);
  mergeModuleCells(sheet, cases);
  return { sheet, endRow };
}


function normalizeMatrix(matrix) {
  return matrix.map((row) => row.map(normalizeValue));
}


function expandMergedModuleValues(matrix) {
  let primaryModule = "";
  let secondaryModule = "";
  return matrix.map((row) => {
    const expanded = row.map(normalizeValue);
    if (expanded[1]) {
      if (expanded[1] !== primaryModule) secondaryModule = "";
      primaryModule = expanded[1];
    } else {
      expanded[1] = primaryModule;
    }
    if (expanded[2]) {
      secondaryModule = expanded[2];
    } else {
      expanded[2] = secondaryModule;
    }
    return expanded;
  });
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


function decodeXmlAttribute(value) {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}


function xmlAttribute(tag, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = tag.match(new RegExp(`(?:\\s|^)${escapedName}="([^"]*)"`));
  return match ? decodeXmlAttribute(match[1]) : "";
}


async function workbookStructureFromXlsx(bytes, sheetName) {
  const zip = await JSZip.loadAsync(bytes);
  const workbookFile = zip.file("xl/workbook.xml");
  const relationshipsFile = zip.file("xl/_rels/workbook.xml.rels");
  if (!workbookFile || !relationshipsFile) {
    throw new Error("工作簿缺少 workbook.xml 或其关系文件");
  }
  const workbookXml = await workbookFile.async("string");
  const relationshipsXml = await relationshipsFile.async("string");
  const sheetTag = [...workbookXml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?sheet\b[^>]*\/?\s*>/g)]
    .map((match) => match[0])
    .find((tag) => xmlAttribute(tag, "name") === sheetName);
  if (!sheetTag) throw new Error(`工作簿中找不到 Sheet：${sheetName}`);
  const relationshipId = xmlAttribute(sheetTag, "r:id");
  const relationshipTag = [...relationshipsXml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*\/?\s*>/g)]
    .map((match) => match[0])
    .find((tag) => xmlAttribute(tag, "Id") === relationshipId);
  if (!relationshipTag) throw new Error(`工作簿关系中找不到 ${relationshipId}`);
  const target = xmlAttribute(relationshipTag, "Target");
  const worksheetPath = target.startsWith("/")
    ? target.slice(1)
    : path.posix.normalize(`xl/${target}`);
  const worksheetFile = zip.file(worksheetPath);
  if (!worksheetFile) throw new Error(`工作簿缺少工作表文件：${worksheetPath}`);
  const worksheetXml = await worksheetFile.async("string");
  return {
    mergeRanges: [...worksheetXml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?mergeCell\b[^>]*\bref="([^"]+)"[^>]*\/?\s*>/g)]
      .map((match) => decodeXmlAttribute(match[1]).toUpperCase()),
    tableParts: Object.keys(zip.files)
      .filter((name) => name.startsWith("xl/tables/") && name.endsWith(".xml"))
      .sort(),
  };
}


function columnNumber(label) {
  return [...label].reduce((value, character) => value * 26 + character.charCodeAt(0) - 64, 0);
}


function bodyMergeTouchesProtectedColumns(range) {
  const match = range.match(/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/);
  if (!match) return true;
  const [, startColumn, startRow, endColumn, endRow] = match;
  return Number(endRow) >= DATA_START_ROW
    && Math.max(columnNumber(startColumn), columnNumber(endColumn)) >= columnNumber("D");
}


function assertBoundaryConfirmations(boundaryConfirmations, finalCases, readiness) {
  if (boundaryConfirmations.schema_version !== "1.0") {
    throw new Error("pending_boundary_confirmations.schema_version 必须为 1.0");
  }
  for (const field of ["run_id", "input_sha256", "rule_release_version"]) {
    if (boundaryConfirmations[field] !== finalCases[field]) {
      throw new Error(`pending_boundary_confirmations.${field} 与 final_cases 不一致`);
    }
  }
  if (boundaryConfirmations.requirement_name !== readiness.requirement_name) {
    throw new Error("pending_boundary_confirmations.requirement_name 与 delivery_readiness 不一致");
  }
  const items = boundaryConfirmations.items;
  if (!Array.isArray(items)) throw new Error("pending_boundary_confirmations.items 必须为数组");
  const expectedStatus = items.length ? "awaiting_user_confirmation" : "clear";
  if (boundaryConfirmations.status !== expectedStatus) {
    throw new Error(`pending_boundary_confirmations.status 应为 ${expectedStatus}`);
  }
  for (const [index, item] of items.entries()) {
    const expectedId = `BOUNDARY-${String(index + 1).padStart(4, "0")}`;
    if (!item || typeof item !== "object" || item.boundary_id !== expectedId) {
      throw new Error(`pending_boundary_confirmations.items[${index}].boundary_id 应为 ${expectedId}`);
    }
    for (const field of ["module", "question", "recommendation"]) {
      if (!normalizeValue(item[field]).trim()) {
        throw new Error(`pending_boundary_confirmations.items[${index}].${field} 不能为空`);
      }
    }
    if (!Array.isArray(item.source_refs) || item.source_refs.length === 0) {
      throw new Error(`pending_boundary_confirmations.items[${index}].source_refs 不能为空`);
    }
  }
  if (readiness.boundary_confirmation_count !== items.length) {
    throw new Error("delivery_readiness.boundary_confirmation_count 与边界确认项数量不一致");
  }
  return items;
}


async function verifyExportedWorkbook(
  outputPath,
  finalCases,
  readiness,
  sourcePacket,
  boundaryConfirmations,
) {
  const input = await FileBlob.load(outputPath);
  const workbook = await SpreadsheetFile.importXlsx(input);
  const sheet = workbook.worksheets.getItem(SHEET_NAME);
  const endRow = DATA_START_ROW + finalCases.cases.length - 1;
  const expected = casesMatrix(finalCases.cases);
  const actual = expandMergedModuleValues(
    normalizeMatrix(sheet.getRange(`A${DATA_START_ROW}:J${endRow}`).values),
  );
  const errors = compareCases(actual, expected);

  const header = ["A9", "B10", "C10", "D10", "E9", "F9", "G9", "H9", "I9", "J9"]
    .map((address) => normalizeValue(sheet.getRange(address).values[0][0]));
  const headerMatches = JSON.stringify(header) === JSON.stringify(DISPLAY_HEADERS);
  if (!headerMatches) {
    errors.push(`A:J 表头顺序不一致：${JSON.stringify(header)}`);
  }
  const title = normalizeValue(sheet.getRange("A1").values[0][0]);
  const titleMatches = title === `${readiness.requirement_name}测试用例`;
  if (!titleMatches) errors.push("标题与需求名称不一致");
  const actualSourceNames = normalizeValue(sheet.getRange("D6").values[0][0]);
  const expectedSourceNames = sourceNames(sourcePacket);
  const sourceNamesMatch = actualSourceNames === expectedSourceNames;
  if (!sourceNamesMatch) {
    errors.push(`本地来源文件不一致：expected=${expectedSourceNames} actual=${actualSourceNames}`);
  }
  const formulas = normalizeMatrix(sheet.getRange("D2:I8").formulas);
  const formulaText = formulas.flat().join("\n").toUpperCase().replaceAll("$", "");
  for (const expectedFormulaToken of [
    "COUNTA",
    "COUNTIF",
    "SUM(I3:I6)",
    "D2",
    `A${DATA_START_ROW}:A${endRow}`,
    `I${DATA_START_ROW}:I${endRow}`,
  ]) {
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
  const expectedModuleMerges = moduleMergeGroups(finalCases.cases).map((group) => group.range.toUpperCase());
  const { mergeRanges: actualMergeRanges, tableParts } = await workbookStructureFromXlsx(bytes, SHEET_NAME);
  const expectedTopMerges = TOP_MERGES.map((range) => range.toUpperCase());
  const expectedMergeRanges = [...expectedTopMerges, ...expectedModuleMerges];
  const missingTopMerges = expectedTopMerges.filter((range) => !actualMergeRanges.includes(range));
  const missingModuleMerges = expectedModuleMerges.filter((range) => !actualMergeRanges.includes(range));
  const unexpectedMergeRanges = actualMergeRanges.filter((range) => !expectedMergeRanges.includes(range));
  const invalidBodyMergeRanges = actualMergeRanges.filter(bodyMergeTouchesProtectedColumns);
  if (missingTopMerges.length) {
    errors.push(`顶部版式合并缺失：${missingTopMerges.join(",")}`);
  }
  if (missingModuleMerges.length) {
    errors.push(`模块合并缺失：${missingModuleMerges.join(",")}`);
  }
  if (unexpectedMergeRanges.length) {
    errors.push(`存在合同外合并区域：${unexpectedMergeRanges.join(",")}`);
  }
  if (invalidBodyMergeRanges.length) {
    errors.push(`D:J 用例正文禁止合并：${invalidBodyMergeRanges.join(",")}`);
  }
  if (tableParts.length) {
    errors.push(`工作簿禁止包含 Excel Table：${tableParts.join(",")}`);
  }
  const boundaryItems = assertBoundaryConfirmations(boundaryConfirmations, finalCases, readiness);
  const topLayoutMatch = headerMatches
    && titleMatches
    && sourceNamesMatch
    && missingTopMerges.length === 0;
  const moduleMergesMatch = missingModuleMerges.length === 0
    && unexpectedMergeRanges.length === 0
    && invalidBodyMergeRanges.length === 0;
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
    top_layout_match: topLayoutMatch,
    module_merges_match: moduleMergesMatch,
    module_merge_ranges: expectedModuleMerges,
    actual_merge_ranges: actualMergeRanges,
    unexpected_merge_ranges: unexpectedMergeRanges,
    invalid_body_merge_ranges: invalidBodyMergeRanges,
    no_excel_tables: tableParts.length === 0,
    boundary_confirmation_count: boundaryItems.length,
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
  const boundaryConfirmations = await readJson(path.join(runDir, "pending_boundary_confirmations.json"));
  assertFinalCases(finalCases, readiness);
  assertBoundaryConfirmations(boundaryConfirmations, finalCases, readiness);
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
  const readback = await verifyExportedWorkbook(
    outputPath,
    finalCases,
    readiness,
    sourcePacket,
    boundaryConfirmations,
  );
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
