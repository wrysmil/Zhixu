import { installFixtureTransport } from "./fixture-transport";
import { FIXTURE_TOKEN } from "./fixture-data";

// This module intentionally imports no production runtime until isolation is installed.
const fixture = installFixtureTransport();
async function start() {
  const [React, { createRoot }, { ClientProvider }, { KnowledgePage }, { KnowledgeEvalPage }, { initializeI18n, setAppLanguage }, { TooltipProvider }] = await Promise.all([
    import("react"), import("react-dom/client"), import("../../../webui/src/providers/ClientProvider"),
    import("../../../webui/src/components/workspace/KnowledgePage"),
    import("../../../webui/src/components/workspace/KnowledgeEvalPage"),
    import("../../../webui/src/i18n"), import("../../../webui/src/components/ui/tooltip"),
    import("../../../webui/src/globals.css"),
  ]);
  await initializeI18n();
  await setAppLanguage("zh-CN");
  function FixtureApp() {
    const [page, setPage] = React.useState(location.hash.includes("knowledge-eval") ? "eval" : "knowledge");
    const [dark, setDark] = React.useState(false);
    React.useEffect(() => { document.documentElement.classList.toggle("dark", dark); }, [dark]);
    React.useEffect(() => {
      const onHash = () => setPage(location.hash.includes("knowledge-eval") ? "eval" : "knowledge");
      window.addEventListener("hashchange", onHash);
      return () => window.removeEventListener("hashchange", onHash);
    }, []);
    return <ClientProvider client={fixture.client} token={FIXTURE_TOKEN} modelName="fixture-no-model">
      <TooltipProvider>
        <div className={dark ? "dark" : ""} style={{ minHeight: "100vh" }}>
          <div className="bg-background text-foreground" style={{ minHeight: "100vh" }}>
            <header aria-label="验收夹具控制栏" style={{ padding: "12px 20px", borderBottom: "2px solid #d97706", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
              <strong style={{ color: "#b45309" }}>测试数据 · 隔离夹具</strong>
              <span>所有修改仅在本页内存中生效；刷新恢复；无模型调用。</span>
              <a href="#/knowledge" aria-current={page === "knowledge" ? "page" : undefined}>知识库</a>
              <a href="#/knowledge-eval" aria-current={page === "eval" ? "page" : undefined}>检索评测</a>
              <button type="button" onClick={() => setDark((value) => !value)}>切换{dark ? "浅色" : "深色"}</button>
              <button type="button" onClick={() => location.reload()}>重置测试数据</button>
            </header>
            <main style={{ maxWidth: 1440, margin: "0 auto", padding: 24 }}>
              {page === "knowledge" ? <KnowledgePage workspaceIds={["fixture-workspace"]} /> : <KnowledgeEvalPage />}
            </main>
          </div>
        </div>
      </TooltipProvider>
    </ClientProvider>;
  }
  createRoot(document.getElementById("fixture-root")!).render(<FixtureApp />);
}
void start().catch((error: unknown) => {
  document.getElementById("fixture-root")!.textContent = `夹具启动失败：${error instanceof Error ? error.message : String(error)}`;
  console.error(error);
});
