#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# 支持 1234、00001234、0x1234 格式的十六进制数字
HEX_PATTERN = r"(?:0[xX])?[0-9A-Fa-f]{1,16}"
HEX_RE = re.compile(rf"^{HEX_PATTERN}$")


def is_hex(value):
    return bool(HEX_RE.fullmatch(value.strip()))


def parse_hex(value):
    value = value.strip()

    if value.lower().startswith("0x"):
        value = value[2:]

    return int(value, 16)


def parse_memory_line(line):
    """
    解析 MEMORY CONFIGURATION 中的区域行。

    常见格式：
        RAM 00000000 00010000 00008000 00008000 RW
        RAM 00000000 00010000 RW
    """
    tokens = line.strip().split()

    if len(tokens) < 3:
        return None

    name = tokens[0]

    if name.lower() in {
        "name",
        "origin",
        "length",
        "used",
        "unused",
        "attributes",
    }:
        return None

    values = []
    index = 1

    while index < len(tokens) and is_hex(tokens[index]):
        values.append(parse_hex(tokens[index]))
        index += 1

    if len(values) < 2:
        return None

    origin = values[0]
    length = values[1]

    # 常见格式：name origin length used unused attributes
    if len(values) >= 4:
        used = values[2]
        unused = values[3]
        has_usage = True
    else:
        used = 0
        unused = length
        has_usage = False

    attrs = " ".join(tokens[index:]).strip()

    return {
        "name": name,
        "origin": origin,
        "length": length,
        "used": used,
        "unused": unused,
        "attrs": attrs,
        "has_usage": has_usage,
    }


def parse_output_section_header(line):
    """
    解析同一行包含 section 名、page、地址和大小的格式，例如：

        .text       0    00001000    00002000
        ramfuncs    0    00008000    00000100

    行首是否缩进不作限制，但会排除：
      - * 地址标记行
      - Total: 等汇总行
      - Os_gen.o、file.obj 等目标文件名
      - 以十六进制地址开头的输入 section 行
    """
    text = line.strip()

    if not text:
        return None

    match = re.match(
        rf"^(?P<name>\S+)\s+"
        rf"(?P<page>\d+)\s+"
        rf"(?P<address>{HEX_PATTERN})\s+"
        rf"(?P<size>{HEX_PATTERN})"
        rf"(?:\s+.*)?$",
        text,
    )

    if not match:
        return None

    name = match.group("name")
    lower_name = name.lower()

    # * 是输出 section 的地址标记，不是 section 名
    if name == "*":
        return None

    # 不要把汇总行当成 section
    if lower_name.rstrip(":") in {
        "total",
        "name",
        "section",
        "output",
        "page",
        "origin",
        "length",
    }:
        return None

    # 输入记录可能被误判为 section 行，例如：
    # Os_gen.o 0 00080000 00001000
    if lower_name.endswith((".o", ".obj", ".lib")):
        return None

    # 以十六进制地址开头的行属于输入记录，不是输出 section
    if is_hex(name):
        return None

    return {
        "name": name,
        "page": int(match.group("page"), 10),
        "address": parse_hex(match.group("address")),
        "size": parse_hex(match.group("size")),
        "inputs": [],
    }


def parse_star_section_header(line):
    """
    解析 section 名单独一行后的 * 地址行。

    例如：
        .os_stack_tcmb
        *          0    00080000    0000ca08     UNINITIALIZED
    """
    match = re.match(
        rf"^\s*\*\s+\d+\s+"
        rf"(?P<address>{HEX_PATTERN})\s+"
        rf"(?P<size>{HEX_PATTERN})"
        rf"(?:\s+.*)?$",
        line,
    )

    if not match:
        return None

    return {
        "address": parse_hex(match.group("address")),
        "size": parse_hex(match.group("size")),
    }


def parse_input_line(line):
    """
    解析输入 section 行。

    例如：
        00080000 00000780 Os_src.a : Os_gen.o (.bss.core0.xxx)
        00080780 00002000              : Os_gen.o (.bss.core0.xxx)

    只接受行首恰好两个十六进制字段，避免误把地址解析成大小。
    """
    tokens = line.strip().split()

    if len(tokens) < 2:
        return None, False

    number_count = 0

    for token in tokens:
        if is_hex(token):
            number_count += 1
        else:
            break

    if number_count == 0:
        return None, False

    description = " ".join(tokens[number_count:]).strip()

    # 一个数字字段通常是续行，不作为输入记录处理
    if number_count == 1:
        return None, bool(description)

    # 三个或更多连续数字字段格式不明确，不猜测大小
    if number_count >= 3:
        return None, bool(description)

    if not description:
        return None, False

    if "--HOLE--" in description:
        return {
            "address": parse_hex(tokens[0]),
            "size": parse_hex(tokens[1]),
            "description": description,
        }, False

    # 普通输入记录应包含库/文件分隔符和括号中的输入 section 名
    if ":" not in description:
        return None, False

    if not re.search(r"\([^)]*\)", description):
        return None, False

    return {
        "address": parse_hex(tokens[0]),
        "size": parse_hex(tokens[1]),
        "description": description,
    }, False


def parse_input_description(description, previous_archive=""):
    """从输入记录中提取库名、目标文件名和输入 section 名。"""
    description = description.strip()

    if "--HOLE--" in description:
        return {
            "archive": "",
            "object": "",
            "input": "--HOLE--",
        }, previous_archive

    archive = ""
    body = description

    # map 文件可能省略连续输入记录中的库名：
    #     : Os_gen.o (.bss.core0.xxx)
    if body.startswith(":"):
        body = body[1:].strip()
        archive = previous_archive
    else:
        parts = re.split(r"\s+:\s+", body, maxsplit=1)

        if len(parts) == 2:
            archive = parts[0].strip()
            body = parts[1].strip()
        else:
            archive = previous_archive

    match = re.match(
        r"^(?P<object>.*?)\s+\((?P<input>.*)\)\s*$",
        body,
    )

    if match:
        object_name = match.group("object").strip()
        input_name = match.group("input").strip()
    else:
        object_name = body.strip()
        input_name = body.strip()

    return {
        "archive": archive,
        "object": object_name,
        "input": input_name,
    }, archive or previous_archive


def parse_map(map_path):
    """解析 map 文件，返回 Memory 区域、输出 section 和跳过的歧义记录数。"""
    memory_regions = []
    output_sections = []
    skipped_ambiguous_lines = 0

    mode = None
    current_section = None
    pending_section_name = None
    previous_archive = ""

    lines = map_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if not stripped:
            continue

        # Memory 区域表开始
        if "MEMORY CONFIGURATION" in upper:
            mode = "memory"
            current_section = None
            pending_section_name = None
            continue

        # 输出 section 表开始
        if (
            "SECTION ALLOCATION MAP" in upper
            or "SECTION ALLOCATION" in upper
        ):
            mode = "sections"
            current_section = None
            pending_section_name = None
            previous_archive = ""
            continue

        # Memory 表结束
        if mode == "memory":
            if (
                "GLOBAL SYMBOLS" in upper
                or "SYMBOLS BY NAME" in upper
                or "SECTION ALLOCATION" in upper
            ):
                mode = "sections" if "SECTION ALLOCATION" in upper else None
                current_section = None
                pending_section_name = None

                if mode != "sections":
                    continue

            if mode == "memory":
                memory = parse_memory_line(line)

                if memory:
                    memory_regions.append(memory)

                continue

        if mode != "sections":
            continue

        # 先处理 section 名单独一行、下一行以 * 开头的格式。
        # 这一步必须在普通 section 行解析之前进行。
        if pending_section_name:
            star_header = parse_star_section_header(line)

            if star_header:
                current_section = {
                    "name": pending_section_name,
                    "page": 0,
                    "address": star_header["address"],
                    "size": star_header["size"],
                    "inputs": [],
                }

                output_sections.append(current_section)
                pending_section_name = None
                previous_archive = ""
                continue

        # 普通的单行输出 section 格式
        output_header = parse_output_section_header(line)

        if output_header:
            current_section = output_header
            output_sections.append(current_section)
            pending_section_name = None
            previous_archive = ""
            continue

        # section 名单独一行，例如：
        #     .os_stack_tcmb
        #
        # 仅匹配以点开头的单独一行，不会把输入记录误认成 section 名。
        if re.fullmatch(r"\s*\.[^\s]+\s*", line):
            pending_section_name = stripped
            current_section = None
            continue

        if current_section is None:
            continue

        # 解析 section 下的输入项
        parsed, ambiguous = parse_input_line(line)

        if ambiguous:
            skipped_ambiguous_lines += 1
            continue

        if not parsed:
            continue

        details, previous_archive = parse_input_description(
            parsed["description"],
            previous_archive,
        )

        # 忽略明显大于所属输出 section 的异常输入记录
        if parsed["size"] > current_section["size"]:
            skipped_ambiguous_lines += 1
            continue

        current_section["inputs"].append({
            "address": parsed["address"],
            "size": parsed["size"],
            "archive": details["archive"],
            "object": details["object"],
            "input": details["input"],
        })

    # 若 Memory 表未提供 used/unused，则根据输出 section 地址估算使用量
    for memory in memory_regions:
        if memory["has_usage"]:
            continue

        start = memory["origin"]
        end = start + memory["length"]
        used = 0

        for section in output_sections:
            section_start = section["address"]
            section_end = section_start + section["size"]

            if section_start >= start and section_start < end:
                used += max(0, min(section_end, end) - section_start)

        memory["used"] = min(used, memory["length"])
        memory["unused"] = max(0, memory["length"] - memory["used"])

    return memory_regions, output_sections, skipped_ambiguous_lines


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TI Map 内存分析</title>
<style>
:root {
  --bg:#f2f5fa; --panel:#fff; --ink:#17243a; --muted:#71809a;
  --line:#e3e9f2; --blue:#4d78f3; --shadow:0 8px 24px rgba(36,57,91,.07);
}
* { box-sizing:border-box; }
body {
  margin:0; color:var(--ink); background:var(--bg);
  font-family:"Segoe UI","Microsoft YaHei",sans-serif; font-size:14px;
}
header {
  padding:23px 28px; color:white;
  background:linear-gradient(115deg,#1a2c50,#24649e 65%,#25879b);
}
header h1 { margin:0 0 5px; font-size:23px; }
header p { margin:0; color:rgba(255,255,255,.78); }
main { max-width:1600px; margin:auto; padding:20px 22px 34px; }
.summary-grid {
  display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  gap:11px; margin-bottom:13px;
}
.panel,.summary-card {
  min-width:0; background:var(--panel); border:1px solid var(--line);
  border-radius:13px; box-shadow:var(--shadow);
}
.summary-card { padding:13px 15px; }
.summary-label,.hint,.stat-label { color:var(--muted); font-size:12px; }
.summary-value { margin-top:4px; font-size:20px; font-weight:750; }
.summary-note { margin-top:2px; color:var(--muted); font-size:11px; }
.warning {
  margin:0 0 12px; padding:10px 12px; color:#76521b;
  background:#fff8e9; border:1px solid #f0d6a5; border-radius:9px;
  font-size:12px;
}
.dashboard {
  display:grid;
  grid-template-columns:minmax(240px,.78fr) minmax(0,1.35fr) minmax(0,1.25fr);
  gap:13px; align-items:start;
}
.panel { padding:16px; }
.panel h2 { margin:0 0 4px; font-size:16px; }
.hint { margin-bottom:12px; line-height:1.5; }
.scroll-list {
  display:grid; gap:8px; max-height:min(60vh,440px);
  overflow:auto; align-content:start; padding:1px 5px 3px 1px;
  overscroll-behavior:contain; scrollbar-gutter:stable;
}
.scroll-list::-webkit-scrollbar,.table-wrap::-webkit-scrollbar {
  width:8px; height:8px;
}
.scroll-list::-webkit-scrollbar-thumb,.table-wrap::-webkit-scrollbar-thumb {
  background:#cbd4e1; border-radius:8px;
}
.scroll-list::-webkit-scrollbar-track,.table-wrap::-webkit-scrollbar-track {
  background:#f0f3f7;
}
.memory-item {
  width:100%; padding:10px 11px; text-align:left; background:white;
  border:1px solid var(--line); border-radius:10px; cursor:pointer;
}
.memory-item:hover,.rank-item:hover { border-color:#a9bdf3; }
.memory-item.active,.rank-item.active {
  background:#f2f6ff; border-color:var(--blue);
}
.item-top {
  display:flex; align-items:center; justify-content:space-between; gap:8px;
}
.item-name { font-weight:650; overflow-wrap:anywhere; }
.item-value { font-weight:700; white-space:nowrap; }
.item-meta { margin-top:4px; color:var(--muted); font-size:11px; overflow-wrap:anywhere; }
.track { height:8px; margin-top:8px; overflow:hidden; background:#edf1f7; border-radius:8px; }
.fill { height:100%; border-radius:8px; }
.rank-item { padding:9px 10px; border:1px solid var(--line); border-radius:9px; cursor:pointer; }
.detail { margin-top:13px; }
.detail-head { display:flex; justify-content:space-between; gap:10px; margin-bottom:12px; }
.detail-head h2 { margin:0; font-size:18px; }
.detail-subtitle { margin-top:4px; color:var(--muted); font-size:12px; }
.badge { padding:6px 10px; border-radius:9px; color:#315fc2; background:#edf3ff; font-size:12px; white-space:nowrap; }
.badge.warn { color:#a35a16; background:#fff3e7; }
.badge.danger { color:#a72c37; background:#ffebed; }
.stats { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px; }
.stat { min-width:0; padding:10px; background:#fbfcfe; border:1px solid var(--line); border-radius:9px; }
.stat-value { margin-top:4px; font-size:15px; font-weight:700; overflow-wrap:anywhere; }
.toolbar {
  display:grid; grid-template-columns:minmax(180px,1fr) minmax(180px,250px) auto auto;
  gap:8px; margin:12px 0;
}
input,select,button {
  min-width:0; min-height:37px; padding:8px 10px; color:var(--ink);
  background:white; border:1px solid #dce3ed; border-radius:8px; font:inherit;
}
button { cursor:pointer; }
button:hover { border-color:var(--blue); color:var(--blue); }
.table-wrap { max-height:560px; overflow:auto; border:1px solid var(--line); border-radius:9px; }
table { width:100%; table-layout:fixed; border-collapse:collapse; font-size:12px; }
col.out-col { width:15%; } col.in-col { width:23%; } col.mod-col { width:22%; }
col.addr-col { width:13%; } col.size-col { width:12%; } col.pct-col { width:15%; }
th,td {
  padding:9px 8px; text-align:left; vertical-align:top;
  border-bottom:1px solid var(--line); overflow-wrap:anywhere; word-break:break-word;
}
th { position:sticky; top:0; z-index:1; color:#5d6d85; background:#f7f9fc; }
td.numeric,th.numeric {
  text-align:right; white-space:nowrap; overflow-wrap:normal; word-break:normal;
  font-variant-numeric:tabular-nums;
}
tbody tr:hover { background:#f5f8ff; }
.pill { display:inline-block; padding:2px 7px; color:#4168c1; background:#edf3ff; border-radius:20px; font-size:11px; }
.status,.empty { color:var(--muted); font-size:11px; }
.status { margin-top:8px; }
.empty { padding:22px 10px; text-align:center; }
@media(max-width:1100px) {
  .dashboard { grid-template-columns:minmax(230px,.8fr) minmax(0,1.2fr); }
  .module-panel { grid-column:1/-1; }
}
@media(max-width:720px) {
  main { padding:13px 10px 25px; }
  header { padding:19px 14px; }
  .dashboard { grid-template-columns:1fr; }
  .module-panel { grid-column:auto; }
  .stats { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .toolbar { grid-template-columns:1fr 1fr; }
}
@media(max-width:480px) {
  .toolbar { grid-template-columns:1fr; }
  .panel { padding:12px; }
  table { font-size:11px; }
  th,td { padding:7px 5px; }
  .scroll-list { max-height:52vh; }
}
</style>
</head>
<body>
<header>
  <h1>TI Linker Map · 内存分析</h1>
  <p id="sourceName"></p>
</header>
<main>
  <div class="summary-grid" id="summary"></div>
  <div id="warningBox"></div>

  <div class="dashboard">
    <section class="panel">
      <h2>Memory 区域</h2>
      <div class="hint">点击区域查看对应 section；区域列表可单独滚动。</div>
      <div class="scroll-list" id="memoryList"></div>
    </section>
    <section class="panel">
      <h2>Section 占用排行</h2>
      <div class="hint">显示当前 Memory 范围内的 section；点击 section 可筛选明细。</div>
      <div class="scroll-list" id="sectionList"></div>
    </section>
    <section class="panel module-panel">
      <h2>库 / 目标文件占用排行</h2>
      <div class="hint">按已识别输入项大小汇总；点击模块可筛选明细。</div>
      <div class="scroll-list" id="moduleList"></div>
    </section>
  </div>

  <section class="panel detail">
    <div class="detail-head">
      <div>
        <h2 id="detailTitle"></h2>
        <div class="detail-subtitle" id="detailSubtitle"></div>
      </div>
      <div class="badge" id="usageBadge"></div>
    </div>
    <div class="stats" id="stats"></div>
  </section>

  <section class="panel detail">
    <h2 id="tableTitle">输入 section 明细</h2>
    <div class="hint">占比依次为“占所属输出 section”和“占当前 Memory 已用量”。</div>
    <div class="toolbar">
      <input id="search" type="search" placeholder="搜索 section、库或目标文件">
      <select id="sectionFilter">
        <option value="">当前范围内的全部 section</option>
      </select>
      <button id="clearButton" type="button">清除筛选</button>
      <button id="exportButton" type="button">导出 CSV</button>
    </div>
    <div class="table-wrap">
      <table>
        <colgroup>
          <col class="out-col"><col class="in-col"><col class="mod-col">
          <col class="addr-col"><col class="size-col"><col class="pct-col">
        </colgroup>
        <thead>
          <tr>
            <th>输出 section</th><th>输入 section</th><th>库 / 目标文件</th>
            <th class="numeric">地址</th><th class="numeric">大小</th><th class="numeric">占比</th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
    <div class="status" id="tableStatus"></div>
  </section>
</main>

<script>
const DATA = __DATA__;
const SOURCE = __SOURCE__;

const memoryRegions = DATA.memory || [];
const sections = DATA.sections || [];
const skippedLines = Number(DATA.skipped_ambiguous_lines || 0);
const COLORS = ["#4d78f3","#20afb8","#8b68ed","#ed9851","#2aa984","#d96b9e","#628fc9","#bc983b"];

let selectedMemoryIndex = -1;
let selectedSectionName = "";
let selectedModuleKey = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
  })[char]);
}

function bytes(value) {
  value = Number(value) || 0;
  if (value < 1024) return value.toLocaleString() + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(2) + " KiB";
  return (value / (1024 * 1024)).toFixed(2) + " MiB";
}

function address(value) {
  return "0x" + Number(value).toString(16).toUpperCase();
}

function currentMemory() {
  return selectedMemoryIndex < 0 || selectedMemoryIndex >= memoryRegions.length
    ? null : memoryRegions[selectedMemoryIndex];
}

function sectionsForMemory(memory) {
  if (!memory) return sections;
  const start = Number(memory.origin);
  const end = start + Number(memory.length);
  return sections.filter(section => {
    const sectionStart = Number(section.address);
    const sectionEnd = sectionStart + Math.max(Number(section.size), 1);
    return sectionStart < end && sectionEnd > start;
  });
}

function currentSections() {
  return sectionsForMemory(currentMemory());
}

function currentUsed() {
  const memory = currentMemory();
  if (memory) return Number(memory.used) || 0;
  return memoryRegions.reduce((sum, item) => sum + (Number(item.used) || 0), 0);
}

function currentCapacity() {
  const memory = currentMemory();
  if (memory) return Number(memory.length) || 0;
  return memoryRegions.reduce((sum, item) => sum + (Number(item.length) || 0), 0);
}

function totalSectionSize(list) {
  return list.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
}

function moduleKey(row) {
  return JSON.stringify([row.archive || "", row.object || ""]);
}

function moduleName(row) {
  return [row.archive, row.object].filter(Boolean).join(" : ") || "(未识别模块)";
}

function makeRows(sectionList) {
  const rows = [];
  sectionList.forEach(section => {
    (section.inputs || []).forEach(input => {
      rows.push({
        output: section.name,
        outputSize: Number(section.size) || 0,
        input: input.input || "",
        archive: input.archive || "",
        object: input.object || "",
        address: Number(input.address) || 0,
        size: Number(input.size) || 0
      });
    });
  });
  return rows;
}

function renderSummary() {
  const used = memoryRegions.reduce((sum, item) => sum + (Number(item.used) || 0), 0);
  const capacity = memoryRegions.reduce((sum, item) => sum + (Number(item.length) || 0), 0);
  const rowCount = makeRows(sections).length;
  const percent = capacity > 0 ? used / capacity * 100 : 0;

  const cards = [
    ["Memory 区域", memoryRegions.length, "来自 MEMORY CONFIGURATION"],
    ["Memory 已用", bytes(used), "容量 " + bytes(capacity)],
    ["整体使用率", percent.toFixed(2) + "%", "剩余 " + bytes(Math.max(0, capacity - used))],
    ["输出 section", sections.length, "输出 section 大小合计 " + bytes(totalSectionSize(sections))],
    ["已识别输入项", rowCount, "仅统计字段格式明确的输入记录"]
  ];

  document.getElementById("summary").innerHTML = cards.map(card => `
    <div class="summary-card">
      <div class="summary-label">${esc(card[0])}</div>
      <div class="summary-value">${esc(card[1])}</div>
      <div class="summary-note">${esc(card[2])}</div>
    </div>
  `).join("");
}

function renderWarning() {
  const box = document.getElementById("warningBox");
  box.innerHTML = skippedLines > 0 ? `
    <div class="warning">
      为避免把地址误当成大小，解析器跳过了 <strong>${skippedLines}</strong>
      条字段格式不明确的输入记录。Memory 和输出 section 统计不受影响。
    </div>` : "";
}

function renderMemoryList() {
  const list = document.getElementById("memoryList");
  const used = memoryRegions.reduce((sum, item) => sum + (Number(item.used) || 0), 0);
  const capacity = memoryRegions.reduce((sum, item) => sum + (Number(item.length) || 0), 0);
  const allPercent = capacity > 0 ? used / capacity * 100 : 0;

  let html = `
    <button type="button" class="memory-item ${selectedMemoryIndex < 0 ? "active" : ""}" data-index="-1">
      <div class="item-top"><span class="item-name">全部 Memory</span><span class="item-value">${bytes(used)}</span></div>
      <div class="item-meta">总容量 ${bytes(capacity)} · 使用率 ${allPercent.toFixed(2)}%</div>
      <div class="track"><div class="fill" style="width:${Math.min(100, allPercent)}%;background:${COLORS[0]}"></div></div>
    </button>`;

  html += memoryRegions.map((memory, index) => {
    const cap = Number(memory.length) || 0;
    const memUsed = Number(memory.used) || 0;
    const percent = cap > 0 ? memUsed / cap * 100 : 0;
    let color = COLORS[index % COLORS.length];
    if (percent >= 90) color = "#df5a66";
    else if (percent >= 80) color = "#ed9851";

    return `
      <button type="button" class="memory-item ${selectedMemoryIndex === index ? "active" : ""}" data-index="${index}">
        <div class="item-top"><span class="item-name">${esc(memory.name)}</span><span class="item-value">${bytes(memUsed)}</span></div>
        <div class="item-meta">${address(memory.origin)} · 容量 ${bytes(cap)} · 使用率 ${percent.toFixed(2)}%</div>
        <div class="track"><div class="fill" style="width:${Math.min(100, percent)}%;background:${color}"></div></div>
      </button>`;
  }).join("");

  list.innerHTML = html;
  list.querySelectorAll(".memory-item").forEach(button => {
    button.addEventListener("click", () => {
      selectedMemoryIndex = Number(button.dataset.index);
      selectedSectionName = "";
      selectedModuleKey = "";
      renderAll();
    });
  });
}

function renderSectionList() {
  const list = document.getElementById("sectionList");
  const current = currentSections();

  if (!current.length) {
    list.innerHTML = '<div class="empty">没有匹配到输出 section。</div>';
    return;
  }

  const maxSize = Math.max(1, ...current.map(item => Number(item.size) || 0));
  const used = currentUsed();

  list.innerHTML = [...current].sort((a, b) => b.size - a.size).map((section, index) => {
    const size = Number(section.size) || 0;
    const percent = used > 0 ? size / used * 100 : 0;
    const width = size / maxSize * 100;

    return `
      <div class="rank-item ${selectedSectionName === section.name ? "active" : ""}" data-section="${esc(section.name)}">
        <div class="item-top">
          <span class="item-name">${esc(section.name)}</span>
          <span class="item-value">${bytes(size)} · ${percent.toFixed(2)}%</span>
        </div>
        <div class="track"><div class="fill" style="width:${width}%;background:${COLORS[index % COLORS.length]}"></div></div>
        <div class="item-meta">地址 ${address(section.address)} · 已识别输入项 ${(section.inputs || []).length} 条</div>
      </div>`;
  }).join("");

  list.querySelectorAll(".rank-item").forEach(item => {
    item.addEventListener("click", () => {
      const name = item.dataset.section;
      selectedSectionName = selectedSectionName === name ? "" : name;
      selectedModuleKey = "";
      renderAll();
    });
  });
}

function renderModuleList() {
  let rows = makeRows(currentSections());
  if (selectedSectionName) rows = rows.filter(row => row.output === selectedSectionName);

  const groups = new Map();

  rows.forEach(row => {
    const key = moduleKey(row);
    const group = groups.get(key) || {key, name:moduleName(row), size:0, count:0};
    group.size += row.size;
    group.count += 1;
    groups.set(key, group);
  });

  const items = [...groups.values()].sort((a, b) => b.size - a.size);
  const list = document.getElementById("moduleList");

  if (!items.length) {
    list.innerHTML = '<div class="empty">没有可确认的模块占用数据。</div>';
    return;
  }

  const maxSize = Math.max(1, ...items.map(item => item.size));

  list.innerHTML = items.map((item, index) => `
    <div class="rank-item ${selectedModuleKey === item.key ? "active" : ""}" data-key="${encodeURIComponent(item.key)}" title="${esc(item.name)}">
      <div class="item-top"><span class="item-name">${esc(item.name)}</span><span class="item-value">${bytes(item.size)}</span></div>
      <div class="track"><div class="fill" style="width:${item.size / maxSize * 100}%;background:${COLORS[index % COLORS.length]}"></div></div>
      <div class="item-meta">${item.count} 条输入项</div>
    </div>
  `).join("");

  list.querySelectorAll(".rank-item").forEach(item => {
    item.addEventListener("click", () => {
      const key = decodeURIComponent(item.dataset.key);
      selectedModuleKey = selectedModuleKey === key ? "" : key;
      renderAll();
    });
  });
}

function renderDetails() {
  const memory = currentMemory();
  const current = currentSections();
  const used = currentUsed();
  const capacity = currentCapacity();

  const unused = memory
    ? Number(memory.unused) || 0
    : memoryRegions.reduce((sum, item) => sum + (Number(item.unused) || 0), 0);

  const percent = capacity > 0 ? used / capacity * 100 : 0;
  const inputBytes = makeRows(current).reduce((sum, row) => sum + row.size, 0);

  document.getElementById("detailTitle").textContent =
    memory ? memory.name + " 占用情况" : "全部 Memory 占用情况";

  document.getElementById("detailSubtitle").textContent = memory
    ? "地址范围 " + address(memory.origin) + " · 属性 " + (memory.attrs || "未提供")
    : "汇总所有 Memory 区域；点击左侧单个区域可查看详情";

  const badge = document.getElementById("usageBadge");
  badge.textContent = "使用率 " + percent.toFixed(2) + "%";
  badge.className = "badge";
  if (percent >= 90) badge.classList.add("danger");
  else if (percent >= 80) badge.classList.add("warn");

  document.getElementById("stats").innerHTML = `
    <div class="stat"><div class="stat-label">Memory 已用</div><div class="stat-value">${bytes(used)}</div></div>
    <div class="stat"><div class="stat-label">Memory 剩余</div><div class="stat-value">${bytes(unused)}</div></div>
    <div class="stat"><div class="stat-label">Memory 容量</div><div class="stat-value">${bytes(capacity)}</div></div>
    <div class="stat"><div class="stat-label">输出 section / 已识别输入项</div><div class="stat-value">${bytes(totalSectionSize(current))} / ${bytes(inputBytes)}</div></div>
  `;
}

function renderSectionFilter() {
  const select = document.getElementById("sectionFilter");
  const current = currentSections();

  select.innerHTML = '<option value="">当前范围内的全部 section</option>' +
    current.map(section => `
      <option value="${esc(section.name)}">${esc(section.name)}（${bytes(section.size)}）</option>
    `).join("");

  if (current.some(section => section.name === selectedSectionName)) {
    select.value = selectedSectionName;
  } else {
    selectedSectionName = "";
    select.value = "";
  }
}

function filteredRows() {
  let rows = makeRows(currentSections());

  if (selectedSectionName) rows = rows.filter(row => row.output === selectedSectionName);
  if (selectedModuleKey) rows = rows.filter(row => moduleKey(row) === selectedModuleKey);

  const query = document.getElementById("search").value.trim().toLowerCase();

  if (query) {
    rows = rows.filter(row =>
      [row.output, row.input, row.archive, row.object]
        .join(" ")
        .toLowerCase()
        .includes(query)
    );
  }

  return rows.sort((a, b) => b.size - a.size);
}

function renderTable() {
  const rows = filteredRows();
  const used = currentUsed();
  const body = document.getElementById("tableBody");

  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">没有匹配到可确认的输入项记录。</td></tr>';
  } else {
    body.innerHTML = rows.map(row => {
      const sectionPercent = row.outputSize > 0 ? row.size / row.outputSize * 100 : 0;
      const memoryPercent = used > 0 ? row.size / used * 100 : 0;

      return `
        <tr>
          <td><span class="pill">${esc(row.output)}</span></td>
          <td>${esc(row.input)}</td>
          <td>${esc(moduleName(row))}</td>
          <td class="numeric">${address(row.address)}</td>
          <td class="numeric">${bytes(row.size)}</td>
          <td class="numeric">${sectionPercent.toFixed(2)}%<br>
            <span style="color:#8390a3;font-size:10px">Memory ${memoryPercent.toFixed(2)}%</span>
          </td>
        </tr>`;
    }).join("");
  }

  const memory = currentMemory();
  document.getElementById("tableTitle").textContent =
    memory ? memory.name + " 输入 section 明细" : "全部 Memory 输入 section 明细";
  document.getElementById("tableStatus").textContent = rows.length + " 条记录";
}

function renderAll() {
  renderWarning();
  renderMemoryList();
  renderSectionFilter();
  renderDetails();
  renderSectionList();
  renderModuleList();
  renderTable();
}

document.getElementById("search").addEventListener("input", renderTable);

document.getElementById("sectionFilter").addEventListener("change", event => {
  selectedSectionName = event.target.value;
  selectedModuleKey = "";
  renderAll();
});

document.getElementById("clearButton").addEventListener("click", () => {
  selectedSectionName = "";
  selectedModuleKey = "";
  document.getElementById("search").value = "";
  renderAll();
});

document.getElementById("exportButton").addEventListener("click", () => {
  const rows = filteredRows();
  const used = currentUsed();

  const csv = [[
    "输出 section",
    "输入 section",
    "库",
    "目标文件",
    "地址",
    "大小(Byte)",
    "大小(KiB)",
    "占输出 section(%)",
    "占当前 Memory(%)"
  ]];

  rows.forEach(row => {
    csv.push([
      row.output,
      row.input,
      row.archive,
      row.object,
      address(row.address),
      row.size,
      (row.size / 1024).toFixed(4),
      row.outputSize > 0 ? (row.size / row.outputSize * 100).toFixed(4) : "0",
      used > 0 ? (row.size / used * 100).toFixed(4) : "0"
    ]);
  });

  const content = csv.map(row =>
    row.map(value => '"' + String(value ?? "").replace(/"/g, '""') + '"').join(",")
  ).join("\r\n");

  const blob = new Blob(["\uFEFF" + content], {type:"text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "map_analysis.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});

document.getElementById("sourceName").textContent = "来源：" + SOURCE;

renderSummary();
renderAll();
</script>
</body>
</html>
"""


def write_html_report(map_path, output_path):
  memory_regions, output_sections, skipped = parse_map(map_path)

  data = {
    "memory": memory_regions,
    "sections": output_sections,
    "skipped_ambiguous_lines": skipped,
  }

  data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
  html = HTML_TEMPLATE.replace("__DATA__", data_json)
  html = html.replace(
    "__SOURCE__",
    json.dumps(map_path.name, ensure_ascii=False),
  )
  output_path.write_text(html, encoding="utf-8")

  input_count = sum(
    len(section["inputs"])
    for section in output_sections
  )

  return memory_regions, output_sections, input_count, skipped


def main():
  parser = argparse.ArgumentParser(
    description="Analyze a TI linker map and generate an HTML report."
  )
  parser.add_argument(
    "map_file",
    nargs="?",
    type=Path,
    help="TI linker map file (omit to open the desktop GUI)",
  )
  parser.add_argument(
    "-o",
    "--output",
    type=Path,
    help="HTML report path (defaults to the map file with an .html extension)",
  )
  args = parser.parse_args()

  if args.map_file is not None:
    if not args.map_file.is_file():
      parser.error(f"map file does not exist: {args.map_file}")

    output_path = args.output or args.map_file.with_suffix(".html")

    try:
      memory_regions, output_sections, input_count, skipped = write_html_report(
        args.map_file,
        output_path,
      )
    except Exception as exc:
      parser.error(f"failed to generate the report: {exc}")

    print(f"Generated: {output_path}")
    print(f"Memory regions: {len(memory_regions)}")
    print(f"Output sections: {len(output_sections)}")
    print(f"Recognized input entries: {input_count}")
    print(f"Skipped ambiguous records: {skipped}")

    if not output_sections:
      print("Warning: no output sections found; check for SECTION ALLOCATION MAP.")
    return

  if args.output is not None:
    parser.error("--output can only be used when a map file is provided")

  try:
    root = tk.Tk()
  except tk.TclError as exc:
    parser.error(
      "no graphical display is available. Pass a map file to generate an "
      "HTML report without a GUI, for example:\n"
      f"  python {Path(__file__).name} <map-file>\n"
      f"Tkinter error: {exc}"
    )
  else:

    root.title("TI Linker Map 分析工具")
    root.geometry("650x230")
    root.resizable(False, False)

    map_var = tk.StringVar()
    output_var = tk.StringVar()
    status_var = tk.StringVar(value="请选择一个 TI Linker map 文件。")

    def choose_map():
        filename = filedialog.askopenfilename(
            title="选择 map 文件",
            filetypes=[
                ("TI Linker map 文件", "*.map"),
                ("所有文件", "*.*"),
            ],
        )

        if filename:
            map_path = Path(filename)
            map_var.set(str(map_path))

            # 默认输出为 map 文件同目录、同名的 HTML 文件
            output_var.set(str(map_path.with_suffix(".html")))
            status_var.set("已选择 map 文件，点击“生成 HTML”开始分析。")

    def choose_output():
        filename = filedialog.asksaveasfilename(
            title="选择 HTML 输出位置",
            defaultextension=".html",
            filetypes=[
                ("HTML 文件", "*.html"),
                ("所有文件", "*.*"),
            ],
            initialfile=Path(output_var.get()).name if output_var.get() else "",
        )

        if filename:
            output_var.set(filename)

    def generate_html():
        map_path = Path(map_var.get().strip())

        if not map_path.is_file():
            messagebox.showerror("错误", "请先选择有效的 map 文件。")
            return

        output_text = output_var.get().strip()
        output_path = (
            Path(output_text)
            if output_text
            else map_path.with_suffix(".html")
        )

        try:
            memory_regions, output_sections, input_count, skipped = write_html_report(
                map_path,
                output_path,
            )
            result = (
                f"已生成：{output_path}\n"
                f"Memory 区域：{len(memory_regions)}\n"
                f"输出 section：{len(output_sections)}\n"
                f"已识别输入项：{input_count}\n"
                f"跳过的歧义记录：{skipped}"
            )

            if not output_sections:
                result += (
                    "\n\n警告：没有识别到输出 section。"
                    "\n请确认 map 文件包含 SECTION ALLOCATION MAP 区域。"
                )

            status_var.set(f"生成完成：{output_path}")
            messagebox.showinfo("分析完成", result)

        except Exception as exc:
            messagebox.showerror(
                "生成失败",
                f"处理 map 文件时发生错误：\n{exc}",
            )
            status_var.set("生成失败。")

    # map 文件选择行
    tk.Label(root, text="Map 文件：").grid(
        row=0, column=0, padx=10, pady=(20, 10), sticky="e"
    )
    tk.Entry(root, textvariable=map_var, width=65).grid(
        row=0, column=1, padx=5, pady=(20, 10)
    )
    tk.Button(root, text="浏览...", command=choose_map).grid(
        row=0, column=2, padx=10, pady=(20, 10)
    )

    # HTML 输出路径行
    tk.Label(root, text="输出 HTML：").grid(
        row=1, column=0, padx=10, pady=10, sticky="e"
    )
    tk.Entry(root, textvariable=output_var, width=65).grid(
        row=1, column=1, padx=5, pady=10
    )
    tk.Button(root, text="浏览...", command=choose_output).grid(
        row=1, column=2, padx=10, pady=10
    )

    # 生成按钮与状态提示
    tk.Button(
        root,
        text="生成 HTML",
        command=generate_html,
        width=18,
        height=2,
    ).grid(row=2, column=1, pady=15)

    tk.Label(
        root,
        textvariable=status_var,
        anchor="w",
        fg="gray",
    ).grid(row=3, column=0, columnspan=3, padx=15, sticky="w")

    root.mainloop()


if __name__ == "__main__":
    main()