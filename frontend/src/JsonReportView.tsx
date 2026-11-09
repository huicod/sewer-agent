import { useState } from "react";

type Path = string[];
const PAGE_SIZE = 60;
const FIELD_NAMES: Record<string, string> = {
  schema_version: "数据版本", session: "会话信息", session_id: "会话编号", generated_at: "生成时间",
  is_final: "最终快照", turns: "对话记录", asset_groups: "影像来源", input_asset: "输入影像",
  asset_id: "资产编号", version: "版本", sources: "检查来源", task: "任务信息", task_id: "任务编号",
  root_task_id: "根任务编号", role: "来源角色", request_id: "请求编号", instruction: "检查要求",
  created_at: "创建时间", updated_at: "更新时间", snapshot: "检查结果", runtime_status: "执行状态",
  inspection_result: "检查结论", findings: "已保存发现", finding_id: "发现编号", canonical_label: "类别",
  open_label: "补充类别", review_status: "复核状态", source: "记录来源", score: "原候选模型分数",
  confirmed_by: "确认依据", evidence: "影像证据", asset: "证据资产", region: "图像区域",
  frame_index: "帧序号", timestamp_seconds: "时间（秒）", coverage: "覆盖情况", tool_calls: "工具调用",
  enhancement_selection: "增强选择", enhancement: "增强结果", enhancement_review: "增强复核",
  geometry: "几何证据", image_evidence: "检测与复核证据", text: "用户内容", reply: "回复内容",
  status: "状态", stage: "处理阶段", inspection_mode: "检查方式", frame_source: "视频帧来源",
  parent_task_id: "上级任务", overall_progress: "总进度", processed_frames: "处理帧数",
};

function entries(value: unknown): [string, unknown][] {
  return value !== null && typeof value === "object" ? Object.entries(value) : [];
}

function expandable(value: unknown): boolean {
  return value !== null && typeof value === "object";
}

function describe(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === "object") return `${Object.keys(value).length} 个字段`;
  return String(value);
}

function fieldName(key: string): string {
  return FIELD_NAMES[key] ?? key;
}

function atPath(data: unknown, path: Path): unknown {
  return path.reduce<unknown>((value, key) => {
    if (value === null || typeof value !== "object") return undefined;
    return (value as Record<string, unknown>)[key];
  }, data);
}

function pathLabel(path: Path): string {
  return path.length ? path.map((key) => /^\d+$/.test(key) ? `[${key}]` : fieldName(key)).join(" / ") : "报告概览";
}

function TreeBranch({ label, value, path, selected, onSelect }: {
  label: string; value: unknown; path: Path; selected: string; onSelect: (path: Path) => void;
}) {
  const [expanded, setExpanded] = useState(path.length === 0);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const children = entries(value);
  const key = JSON.stringify(path);
  return <li>
    <div className={`json-report-tree__row${selected === key ? " json-report-tree__row--selected" : ""}`}>
      {children.length ? <button type="button" className="json-report-tree__toggle"
        aria-label={`${expanded ? "收起" : "展开"}${label}`} aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}>{expanded ? "▾" : "▸"}</button>
        : <span className="json-report-tree__toggle" aria-hidden="true">·</span>}
      <button type="button" className="json-report-tree__select" aria-current={selected === key ? "true" : undefined}
        onClick={() => onSelect(path)} title={path.join(".") || "$"}>
        <span>{label}</span>{expandable(value) && <small>{Array.isArray(value) ? `[${children.length}]` : `{${children.length}}`}</small>}
      </button>
    </div>
    {expanded && children.length > 0 && <ul>
      {children.slice(0, limit).map(([childKey, childValue]) => <TreeBranch key={childKey}
        label={Array.isArray(value) ? `[${childKey}]` : fieldName(childKey)} value={childValue}
        path={[...path, childKey]} selected={selected} onSelect={onSelect} />)}
      {children.length > limit && <li><button className="json-report-more" type="button"
        onClick={() => setLimit((current) => current + PAGE_SIZE)}>再显示 {Math.min(PAGE_SIZE, children.length - limit)} 项</button></li>}
    </ul>}
  </li>;
}

function NodeTable({ value, path, onSelect }: { value: unknown; path: Path; onSelect: (path: Path) => void }) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  if (!expandable(value)) return <pre className="json-report-scalar">{describe(value)}</pre>;
  const all = entries(value);
  if (!all.length) return <p className="json-report-empty">{Array.isArray(value) ? "空列表 []" : "空对象 {}"}</p>;
  const displayed = all.slice(0, limit);
  const rows = Array.isArray(value) && displayed.every(([, item]) => item !== null && typeof item === "object" && !Array.isArray(item));
  const columns = rows ? [...new Set(displayed.flatMap(([, item]) => entries(item)
    .filter(([, cell]) => !expandable(cell)).map(([key]) => key)))].slice(0, 6) : [];
  return <>
    <div className="json-report-table-scroll">
      {rows && columns.length > 0 ? <table className="json-report-table"><thead><tr>
        <th scope="col">序号</th>{columns.map((key) => <th scope="col" key={key} title={key}>{fieldName(key)}</th>)}
        <th scope="col">完整记录</th>
      </tr></thead><tbody>{displayed.map(([key, item]) => <tr key={key}>
        <th scope="row">{Number(key) + 1}</th>
        {columns.map((column) => <td key={column}>{Object.hasOwn(item as object, column) ? describe((item as Record<string, unknown>)[column]) : "未提供"}</td>)}
        <td><button type="button" onClick={() => onSelect([...path, key])}>查看 [{key}]</button></td>
      </tr>)}</tbody></table> : <table className="json-report-table"><thead><tr>
        <th scope="col">字段</th><th scope="col">已保存的值</th>
      </tr></thead><tbody>{displayed.map(([key, item]) => <tr key={key}>
        <th scope="row"><span>{fieldName(key)}</span><code>{key}</code></th>
        <td>{expandable(item) ? <button type="button" onClick={() => onSelect([...path, key])}
          aria-label={`查看${fieldName(key)}`}>{describe(item)} <span aria-hidden="true">→</span></button> : describe(item)}</td>
      </tr>)}</tbody></table>}
    </div>
    {rows && columns.length > 0 && <p className="json-report-hint">表格展示主要字段；选择“完整记录”可查看全部字段和嵌套证据。</p>}
    {all.length > limit && <button className="json-report-more" type="button"
      onClick={() => setLimit((current) => current + PAGE_SIZE)}>显示更多（已显示 {limit} / {all.length}）</button>}
  </>;
}

export function JsonReportView({ data }: { data: unknown }) {
  const [path, setPath] = useState<Path>([]);
  const selected = atPath(data, path);
  return <div className="json-report">
    <nav className="json-report-tree" aria-label="JSON 数据结构">
      <h3>数据结构</h3>
      <ul><TreeBranch label="报告概览" value={data} path={[]} selected={JSON.stringify(path)} onSelect={setPath} /></ul>
    </nav>
    <section className="json-report-selection" aria-label="JSON 选中内容">
      <header><h3>{pathLabel(path)}</h3>{path.length > 0 && <button type="button" onClick={() => setPath(path.slice(0, -1))}>返回上一级</button>}</header>
      <code className="json-report-path">${path.map((key) => /^\d+$/.test(key) ? `[${key}]` : `.${key}`).join("")}</code>
      <NodeTable key={JSON.stringify(path)} value={selected} path={path} onSelect={setPath} />
      <p className="json-report-hint">字段和值直接来自当前报告。各检查来源单独保留，不跨任务合计缺陷数。</p>
    </section>
  </div>;
}
