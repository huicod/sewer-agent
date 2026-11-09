import { CopyOutlined, DownloadOutlined, ExportOutlined, PrinterOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Drawer, Radio, Spin, Tag } from "antd";
import { useEffect, useRef, useState } from "react";

import { getInspectionReport, getInspectionReportHtml, inspectionReportUrl } from "./api";
import { JsonReportView } from "./JsonReportView";
import { STATUS_TEXT } from "./taskPresentation";
import type { InspectionReport, ReportFormat, ReportSource } from "./types";
import "./report-viewer.css";

export type { ReportFormat, ReportSource } from "./types";
export type ReportViewerProps = {
  source: ReportSource;
  initialFormat: ReportFormat;
  open: boolean;
  onClose: () => void;
};

type LoadedReport = { key: string; json?: InspectionReport; html?: string; jsonError?: string; htmlError?: string };
const errorText = (error: unknown) => error instanceof Error ? error.message : "报告读取失败，请重试";

function prepareDocument(html: string): string {
  const document = new DOMParser().parseFromString(html, "text/html");
  // fetch() does not transfer response CSP to srcDoc. Keep the document inert and
  // limit evidence images to this application while allowing its inline print CSS.
  const policy = document.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content = `default-src 'none'; img-src ${window.location.origin}; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`;
  document.head.prepend(policy);
  document.querySelectorAll("a[href]").forEach((anchor) => {
    if (anchor.getAttribute("href")!.startsWith("#")) return;
    anchor.setAttribute("target", "_blank");
    anchor.setAttribute("rel", "noopener noreferrer");
    anchor.setAttribute("href", new URL(anchor.getAttribute("href")!, window.location.href).href);
  });
  document.querySelectorAll("img[src]").forEach((image) => {
    image.setAttribute("src", new URL(image.getAttribute("src")!, window.location.href).href);
  });
  return `<!doctype html>\n${document.documentElement.outerHTML}`;
}

function saveFile(content: string, mime: string, name: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ReportViewer({ source, initialFormat, open, onClose }: ReportViewerProps) {
  const [format, setFormat] = useState<ReportFormat>(initialFormat);
  const [raw, setRaw] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<LoadedReport | null>(null);
  const [feedback, setFeedback] = useState<{ error: boolean; text: string } | null>(null);
  const [frameReady, setFrameReady] = useState(false);
  const iframe = useRef<HTMLIFrameElement>(null);
  const key = `${source.kind}:${source.id}`;
  const current = loaded?.key === key ? loaded : null;

  useEffect(() => {
    setFormat(initialFormat);
    setRaw(false);
    setFeedback(null);
  }, [open, key, initialFormat]);

  useEffect(() => {
    if (!open) { setLoaded(null); return; }
    let active = true;
    setLoaded({ key });
    setFeedback(null);
    setFrameReady(false);
    void getInspectionReport(source).then((json) => {
      if (active) setLoaded((state) => ({ ...state, key, json }));
    }, (error: unknown) => {
      if (active) setLoaded((state) => ({ ...state, key, jsonError: errorText(error) }));
    });
    void getInspectionReportHtml(source).then((html) => {
      const prepared = prepareDocument(html);
      if (active) setLoaded((state) => ({ ...state, key, html: prepared }));
    }).catch((error: unknown) => {
      if (active) setLoaded((state) => ({ ...state, key, htmlError: errorText(error) }));
    });
    return () => { active = false; };
  }, [open, source.kind, source.id, key, attempt]);

  const json = current?.json;
  const content = format === "html" ? current?.html : json ? JSON.stringify(json, null, 2) : undefined;
  const failure = format === "html" ? current?.htmlError : current?.jsonError;
  const busy = !content && !failure;

  async function copyJson() {
    if (!json) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(json, null, 2));
      setFeedback({ error: false, text: "已复制完整 JSON 报告" });
    } catch {
      setFeedback({ error: true, text: "无法访问剪贴板，请使用下载，或在原始 JSON 中选择并复制。" });
    }
  }

  function printReport() {
    try {
      if (!iframe.current?.contentWindow) throw new Error("no report frame");
      iframe.current.contentWindow.focus();
      iframe.current.contentWindow.print();
    } catch {
      setFeedback({ error: true, text: "当前浏览器无法在此处打印，请选择“新窗口打开”后使用浏览器打印。" });
    }
  }

  function reportFrameLoaded() {
    setFrameReady(true);
    // srcDoc inherits the app's base URL. Keep document outline links inside
    // this report, rather than navigating the iframe back to the workbench.
    const document = iframe.current?.contentDocument;
    document?.querySelectorAll<HTMLAnchorElement>('a[href^="#"]').forEach((anchor) => {
      anchor.addEventListener("click", (event) => {
        const target = document.getElementById(anchor.getAttribute("href")!.slice(1));
        if (target) { event.preventDefault(); target.scrollIntoView(); }
      });
    });
  }

  return <Drawer open={open} onClose={onClose} size="min(1280px, 100vw)" rootClassName="inspection-report-viewer"
    title={<span>{source.kind === "session" ? "会话统一报告" : "单项检查报告"}</span>}>
    <div className="report-viewer-toolbar">
      <Radio.Group optionType="button" buttonStyle="solid" value={format} aria-label="报告格式"
        onChange={(event) => { setFormat(event.target.value as ReportFormat); setFeedback(null); }}
        options={[{ label: "HTML 报告", value: "html" }, { label: "JSON 数据", value: "json" }]} />
      <div className="report-viewer-actions">
        <Button icon={<ReloadOutlined />} onClick={() => setAttempt((value) => value + 1)}>刷新</Button>
        {format === "html" ? <Button icon={<PrinterOutlined />} disabled={!content || !frameReady} onClick={printReport}>打印</Button>
          : <Button icon={<CopyOutlined />} disabled={!json} onClick={() => void copyJson()}>复制 JSON</Button>}
        <Button icon={<DownloadOutlined />} disabled={!content} onClick={() => {
          try { saveFile(content!, format === "html" ? "text/html;charset=utf-8" : "application/json;charset=utf-8",
            `${source.kind === "session" ? "conversation" : "inspection"}-${source.id}.${format}`); }
          catch { setFeedback({ error: true, text: "下载失败，请尝试“新窗口打开”后保存报告。" }); }
        }}>下载</Button>
        <Button icon={<ExportOutlined />} href={inspectionReportUrl(source, format)} target="_blank" rel="noreferrer">新窗口打开</Button>
      </div>
    </div>
    <div className="report-viewer-meta">
      <span>{source.kind === "session" ? "会话编号" : "任务编号"} <code>{source.id}</code></span>
      {json && ("is_final" in json
        ? <Tag color={json.is_final ? "default" : "gold"}>{json.is_final ? "最终快照" : "非最终快照 · 仍有待处理事项"}</Tag>
        : <Tag>{STATUS_TEXT[json.task.snapshot.runtime_status]}</Tag>)}
    </div>
    {feedback && <Alert type={feedback.error ? "error" : "success"} showIcon title={feedback.text} />}
    {failure ? <Alert type="error" showIcon title={failure}
      action={<Button onClick={() => setAttempt((value) => value + 1)}>重新读取报告</Button>} /> : busy ?
      <div className="report-viewer-loading" role="status"><Spin /><span>正在读取报告…</span></div> : format === "html" ?
        <iframe key={`${key}:${attempt}`} ref={iframe} className="report-viewer-document" title="HTML 检查报告预览"
          srcDoc={current?.html} sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-modals"
          onLoad={reportFrameLoaded} /> : json ? <div className="report-viewer-json">
          <div className="report-viewer-json-mode"><Radio.Group optionType="button" value={raw ? "raw" : "structured"}
            aria-label="JSON 显示方式" onChange={(event) => setRaw(event.target.value === "raw")}
            options={[{ label: "结构化查看", value: "structured" }, { label: "原始 JSON", value: "raw" }]} /></div>
          {raw ? <pre className="report-viewer-raw" tabIndex={0} aria-label="完整 JSON 报告">{content}</pre>
            : <JsonReportView key={`${key}:${attempt}`} data={json} />}
        </div> : null}
  </Drawer>;
}
