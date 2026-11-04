import { ApiOutlined, CheckCircleOutlined, SettingOutlined } from "@ant-design/icons";
import { Alert, Button, Checkbox, Drawer, Input, InputNumber, Segmented, Spin, Tag } from "antd";
import { useEffect, useRef, useState } from "react";

import { getApplicationSettings, saveApplicationSettings, testApiConnection } from "./api";
import { defaultImageMode, saveDefaultImageMode } from "./preferences";
import type { ApiConnectionResult, ApiRoleUpdate, ApplicationSettings, InspectionMode } from "./types";
import "./settings.css";

type Role = "llm" | "vlm";
type RoleDraft = ApiRoleUpdate & { api_key: string; clear_api_key: boolean };
const roleNames = { llm: "对话助手", vlm: "视觉复检" };
const blankRole = (): RoleDraft => ({ base_url: "", model: "", api_key: "", clear_api_key: false });

function message(error: unknown) {
  return error instanceof Error ? error.message : "设置操作失败，请稍后重试";
}

export function SettingsPanel({ onClose }: { onClose(): void }) {
  const [saved, setSaved] = useState<ApplicationSettings | null>(null);
  const [roles, setRoles] = useState<Record<Role, RoleDraft>>({ llm: blankRole(), vlm: blankRole() });
  const [sameApi, setSameApi] = useState(false);
  const [timeout, setTimeout] = useState<number | null>(60);
  const [maxTokens, setMaxTokens] = useState<number | null>(4096);
  const [thinking, setThinking] = useState("default");
  const [imageMode, setImageMode] = useState<InspectionMode>(defaultImageMode);
  const [section, setSection] = useState("api");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<Role | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [results, setResults] = useState<Partial<Record<Role, ApiConnectionResult>>>({});
  const alive = useRef(true);
  const busy = saving || testing !== null;

  function populate(value: ApplicationSettings) {
    setSaved(value);
    setRoles({ llm: { ...blankRole(), base_url: value.llm.base_url, model: value.llm.model },
      vlm: { ...blankRole(), base_url: value.vlm.base_url, model: value.vlm.model } });
    setSameApi(false);
    setTimeout(value.api_timeout_seconds);
    setMaxTokens(value.api_max_output_tokens);
    setThinking(value.api_enable_thinking === null ? "default" : String(value.api_enable_thinking));
  }

  useEffect(() => {
    alive.current = true;
    let current = true;
    setLoading(true);
    setError("");
    getApplicationSettings().then((value) => {
      if (current) populate(value);
    }).catch((caught) => { if (current) setError(message(caught)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; alive.current = false; };
  }, [loadAttempt]);

  function changeRole(role: Role, update: Partial<RoleDraft>) {
    setRoles((value) => ({ ...value, [role]: { ...value[role], ...update } }));
    setResults({});
    setSuccess("");
    setError("");
  }

  function cleaned(role: Role): ApiRoleUpdate {
    const draft = roles[role];
    return { base_url: draft.base_url.trim(), model: draft.model.trim(),
      ...(draft.api_key.trim() ? { api_key: draft.api_key.trim() } : {}),
      ...(draft.clear_api_key ? { clear_api_key: true } : {}) };
  }

  async function test(role: Role) {
    if (busy) return;
    const sourceRole = role === "vlm" && sameApi ? "llm" : role;
    const draft = cleaned(sourceRole);
    if (!draft.base_url || !draft.model || roles[sourceRole].clear_api_key) {
      setError("请先填写服务地址、模型名称和可用的 API Key。");
      return;
    }
    setTesting(role);
    setError("");
    setSuccess("");
    try {
      const result = await testApiConnection({ role: sourceRole, base_url: draft.base_url,
        model: draft.model, ...(draft.api_key ? { api_key: draft.api_key } : {}) });
      if (alive.current) setResults((value) => ({ ...value, [role]: result }));
    } catch (caught) {
      if (alive.current) setResults((value) => ({ ...value, [role]: {
        ok: false, model_found: null, message: message(caught),
      } }));
    } finally {
      if (alive.current) setTesting(null);
    }
  }

  async function save() {
    if (!saved || busy) return;
    if (timeout === null || timeout < 1 || timeout > 600 ||
      (maxTokens !== null && (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 8192))) {
      setError("请求超时请设为 1–600 秒；输出上限请设为 1–8192 的整数，或留空使用服务默认值。");
      return;
    }
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const llm = cleaned("llm");
      const next = await saveApplicationSettings({ revision: saved.revision, llm,
        vlm: sameApi ? { base_url: llm.base_url, model: llm.model, reuse_llm_key: true } : cleaned("vlm"),
        api_timeout_seconds: timeout, api_max_output_tokens: maxTokens,
        api_enable_thinking: thinking === "default" ? null : thinking === "true" });
      if (!alive.current) return;
      populate(next);
      setResults({});
      try {
        saveDefaultImageMode(imageMode);
        setSuccess("已保存。新的检查将使用新配置，正在执行的检查保持原配置。");
      } catch {
        setSuccess("API 配置已保存；浏览器未能保存默认检查方式，请检查浏览器存储权限。");
      }
    } catch (caught) {
      if (alive.current) setError(message(caught));
    } finally {
      if (alive.current) setSaving(false);
    }
  }

  function roleCard(role: Role) {
    const draft = roles[role];
    const status = saved?.[role];
    const changedEndpoint = Boolean(status?.key_configured && draft.base_url.trim() !== status.base_url);
    return <section className="settings-card" aria-labelledby={`settings-${role}-heading`}>
      <div className="settings-card__heading">
        <h3 id={`settings-${role}-heading`}>{roleNames[role]}</h3>
        <Tag color={status?.configured ? "success" : "default"}>
          {status?.configured ? "已配置" : "待配置"}
        </Tag>
      </div>
      <p className="settings-hint">{role === "llm" ? "理解检查要求、组织工具调用和解释结果。" : "复核图片与视频证据；所选模型需要支持图片输入。"}</p>
      <div className="settings-preset">
        <span>快捷填写</span>
        <Button size="small" disabled={busy} onClick={() => changeRole(role, {
          base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen3.8-max",
          api_key: "", clear_api_key: false,
        })}>百炼（北京）</Button>
        <span className="settings-hint">也可填写其他兼容服务</span>
      </div>
      <label className="settings-field" htmlFor={`settings-${role}-url`}>
        <span>服务地址 · {roleNames[role]}</span>
        <Input id={`settings-${role}-url`} value={draft.base_url} disabled={busy} autoComplete="off"
          placeholder="https://…/v1" onChange={(event) => changeRole(role, { base_url: event.target.value })} />
      </label>
      <label className="settings-field" htmlFor={`settings-${role}-model`}>
        <span>模型名称 · {roleNames[role]}</span>
        <Input id={`settings-${role}-model`} value={draft.model} disabled={busy} autoComplete="off"
          placeholder="填写服务商提供的模型名称" onChange={(event) => changeRole(role, { model: event.target.value })} />
      </label>
      <label className="settings-field" htmlFor={`settings-${role}-key`}>
        <span>API Key · {roleNames[role]}</span>
        <Input.Password id={`settings-${role}-key`} value={draft.api_key} disabled={busy || draft.clear_api_key}
          autoComplete="new-password" placeholder={status?.key_configured ? "已保存，留空保持原密钥" : "填写 API Key"}
          onChange={(event) => changeRole(role, { api_key: event.target.value })} />
      </label>
      {status?.key_configured && <Checkbox checked={draft.clear_api_key} disabled={busy}
        onChange={(event) => changeRole(role, { clear_api_key: event.target.checked, api_key: "" })}>
        清除已保存的{roleNames[role]}密钥
      </Checkbox>}
      {changedEndpoint && !draft.api_key && <p className="settings-endpoint-hint">
        更换服务地址时，请重新填写对应密钥。
      </p>}
      <div className="settings-test">
        <Button icon={<ApiOutlined aria-hidden="true" />} loading={testing === role} disabled={busy && testing !== role}
          onClick={() => void test(role)}>测试{roleNames[role]}连接</Button>
        {results[role] && <Alert showIcon type={results[role]!.ok ? "success" : "error"}
          title={results[role]!.message} />}
      </div>
    </section>;
  }

  return <Drawer title={<span><SettingOutlined aria-hidden="true" /> 设置</span>} open size={760}
    rootClassName="application-settings" onClose={() => { if (!saving) onClose(); }}
    closable={!saving} keyboard={!saving} mask={{ closable: !saving }}
    footer={<div className="settings-footer">
      <span>API 密钥保存在服务端，不在浏览器中持久保存。</span>
      <Button type="primary" loading={saving} disabled={loading || !saved || testing !== null}
        onClick={() => void save()}>保存设置</Button>
    </div>}>
    <div className="settings-intro"><h2>配置你的检查工作台</h2>
      <p>连接模型服务，设置适合当前工作的检查选项。</p></div>
    {error && <Alert className="settings-feedback" showIcon type="error" title={error}
      action={<Button size="small" disabled={busy || loading}
        title={saved ? "丢弃未保存的修改并读取当前设置" : undefined}
        onClick={() => { setResults({}); setSuccess(""); setLoadAttempt((value) => value + 1); }}>
        {saved ? "丢弃修改并重新读取" : "重新读取"}
      </Button>} />}
    {success && <Alert className="settings-feedback" showIcon icon={<CheckCircleOutlined />} type="success" title={success} />}
    {loading ? <div className="settings-loading"><Spin /><span>正在读取设置…</span></div> : saved && <>
      <Segmented block value={section} options={[{ label: "API 连接", value: "api" }, { label: "基础设置", value: "general" }]}
        onChange={setSection} />
      {section === "api" ? <div className="settings-sections">
        {!saved.llm.configured || !saved.vlm.configured ? <Alert showIcon type="info" title="先配置模型服务，即可使用对话与视觉复检。"
          description="暂未配置时，仍可上传图片并选择纯 YOLO 检查。" /> : null}
        {roleCard("llm")}
        <div className="settings-shared"><Checkbox checked={sameApi} disabled={busy}
          onChange={(event) => { setSameApi(event.target.checked); setResults({}); setSuccess(""); }}>
          视觉复检使用相同的 API 配置
        </Checkbox><p>共用服务地址、模型与密钥；请确认该模型支持图片输入。</p></div>
        {sameApi ? <section className="settings-card"><h3>视觉复检</h3>
          <p className="settings-hint">保存时使用上方对话助手的配置。</p>
          <Button disabled={busy} onClick={() => void test("vlm")}>测试共用连接</Button>
          {results.vlm && <Alert className="settings-feedback" showIcon type={results.vlm.ok ? "success" : "error"}
            title={results.vlm.message} />}
        </section> : roleCard("vlm")}
        <p className="settings-hint">连接测试只读取服务的模型列表，不发送图片或生成请求。连接成功不代表已验证模型的视觉复检能力。</p>
      </div> : <div className="settings-sections">
        <section className="settings-card"><h3>检查偏好</h3>
          <label className="settings-field" htmlFor="settings-image-mode"><span id="settings-image-mode-label">新图片默认检查方式</span>
            <select id="settings-image-mode" aria-labelledby="settings-image-mode-label" value={imageMode} disabled={busy}
              onChange={(event) => { setImageMode(event.target.value as InspectionMode); setSuccess(""); }}>
              <option value="yolo_vlm">YOLO＋VLM 复检</option><option value="yolo_only">纯 YOLO（不复检）</option>
            </select>
          </label><p className="settings-hint">仅保存在当前浏览器，供后续选择的新图片使用。视频与证据帧工具仍使用复检方式。</p>
        </section>
        <section className="settings-card"><h3>模型请求</h3>
          <label className="settings-field" htmlFor="settings-timeout"><span>请求超时（秒）</span>
            <InputNumber id="settings-timeout" aria-label="请求超时（秒）" value={timeout} min={1} max={600} disabled={busy}
              onChange={(value) => { setTimeout(value); setSuccess(""); }} />
          </label>
          <label className="settings-field" htmlFor="settings-tokens"><span>最大输出长度（Token）</span>
            <InputNumber id="settings-tokens" aria-label="最大输出长度（Token）" value={maxTokens} min={1} max={8192} precision={0} disabled={busy}
              placeholder="使用服务默认值" onChange={(value) => { setMaxTokens(value); setSuccess(""); }} />
          </label><p className="settings-hint">较大的上限可能增加响应时间和用量；留空沿用服务默认值。</p>
          <label className="settings-field" htmlFor="settings-thinking"><span id="settings-thinking-label">模型思考模式</span>
            <select id="settings-thinking" aria-labelledby="settings-thinking-label" value={thinking} disabled={busy}
              onChange={(event) => { setThinking(event.target.value); setSuccess(""); }}>
              <option value="default">沿用服务默认</option><option value="false">关闭</option><option value="true">开启</option>
            </select>
          </label><p className="settings-hint">按服务商支持情况选择；开启后可能增加响应时间和用量。</p>
        </section>
      </div>}
    </>}
  </Drawer>;
}
