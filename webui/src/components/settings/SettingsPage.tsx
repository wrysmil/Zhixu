import { ChevronLeft, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { SkillsCatalogSettings } from "@/components/settings/SkillsCatalogSettings";
import { AgentsView } from "@/components/settings/agents/AgentsView";
import { TeamsView } from "@/components/settings/teams/TeamsView";
import { ImageGenerationSettings } from "@/components/settings/capabilities/ImageGenerationSettings";
import { AdvancedSettings } from "@/components/settings/capabilities/SecuritySettings";
import { TranscriptionSettings } from "@/components/settings/capabilities/TranscriptionSettings";
import { WebSettings } from "@/components/settings/capabilities/WebSettings";
import {
  ModelPresetDeleteDialog,
  ModelsSettings,
} from "@/components/settings/models/ModelsSettings";
import {
  ProviderOAuthLoginDialog,
  ProvidersSettings,
  providerFormFromRow,
} from "@/components/settings/models/ProviderSettings";
import { AppearanceSettings, OverviewSettings } from "@/components/settings/overview/OverviewSettings";
import { SettingsSidebar, standaloneSectionTitle } from "@/components/settings/SettingsSidebar";
import {
  ZhixuFeatureInstallDialog,
  SettingsGroup,
  SettingsRow,
} from "@/components/settings/shared/SettingsControls";
import { AppsCatalogSettings } from "@/components/settings/system/AppsSettings";
import {
  AutomationDeleteDialog,
  AutomationEditDialog,
  AutomationsSettings,
} from "@/components/settings/system/AutomationsSettings";
import { ChannelsSettings } from "@/components/settings/system/ChannelsSettings";
import { RuntimeSettings } from "@/components/settings/system/RuntimeSettings";
import type { SettingsController } from "@/components/settings/useSettingsController";
import type { SkillSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Standalone capability pages get the same compact header as the assistant
 * pages: kicker-free title, one line of purpose, and a small mascot. Copy is
 * kept local to the two primary locales so the decorative line never falls back
 * to an English paragraph in Chinese builds.
 */
const STANDALONE_PAGE_COPY: Partial<
  Record<
    string,
    { zh: string; en: string; illustration: string; size: "compact" | "default" }
  >
> = {
  agents: {
    zh: "为不同的工作定义专属助手：提示词、模型、工具与技能都归它们自己。",
    en: "Define focused assistants, each with its own prompt, model, tools and skills.",
    illustration: "/illustrations/zhixu-mascot.webp",
    size: "compact",
  },
  teams: {
    zh: "把多位 Agent 编成一支队伍，由队长拆解目标并分派任务。",
    en: "Turn several agents into a team: the leader splits the goal and hands out the work.",
    illustration: "/illustrations/zhixu-team.webp",
    size: "default",
  },
  apps: {
    zh: "管理 CLI 与 MCP 工具，让助手在对话中直接调用它们。",
    en: "Manage the CLI and MCP tools your assistant can call in a conversation.",
    illustration: "/illustrations/zhixu-toolbox.webp",
    size: "default",
  },
  skills: {
    zh: "查看已安装技能，或从市场补充新的工作方法。",
    en: "Review installed skills, or add new ways of working from the marketplace.",
    illustration: "/illustrations/zhixu-toolbox.webp",
    size: "compact",
  },
  automations: {
    zh: "让助手按计划自己跑起来：定时提醒、周期汇总与后台任务。",
    en: "Let the assistant run on a schedule: reminders, recurring digests and background jobs.",
    illustration: "/illustrations/zhixu-schedule.webp",
    size: "default",
  },
};

interface SettingsPageProps {
  controller: SettingsController;
  theme: "light" | "dark";
  showSidebar: boolean;
  onToggleTheme: () => void;
  onBackToChat: () => void;
  skills: SkillSummary[];
  onLogout?: () => void;
  isRestarting: boolean;
  hostChromeInset: boolean;
}

export function SettingsPage({
  controller,
  theme,
  showSidebar,
  onToggleTheme,
  onBackToChat,
  skills,
  onLogout,
  isRestarting,
  hostChromeInset,
}: SettingsPageProps) {
  const { i18n } = useTranslation();
  const zh = i18n.resolvedLanguage?.startsWith("zh") ?? false;
  const {
    activeSection,
    // applyPayload was removed when MemorySection moved to the standalone
    // Memory page; intentionally not destructured here.
    apiService,
    apiServiceAction,
    apiServiceError,
    apiServiceLoading,
    appsKindFilter,
    appsQuery,
    automationAction,
    automationPendingDelete,
    automationPendingEdit,
    automations,
    automationsError,
    automationsFilter,
    automationsLoading,
    automationsQuery,
    automationsSort,
    beginModelPresetCreation,
    cancelModelPresetCreation,
    changeModelCallOrder,
    channelsQuery,
    cliApps,
    cliAppsAction,
    cliAppsError,
    cliAppsFocusName,
    cliAppsLoading,
    cliAppsMessage,
    closeProviderOAuthFlow,
    completeProviderOAuthResponse,
    createCustomProvider,
    customMcpForm,
    editingProviderKeys,
    error,
    expandedProvider,
    featureCatalog,
    form,
    handleApiServiceAction,
    handleAutomationAction,
    handleAutomationEdit,
    handleCliAppAction,
    handleDeleteModelConfiguration,
    handleImportMcpConfig,
    handleMcpOAuthCancel,
    handleMcpOAuthComplete,
    handleMcpOAuthConnect,
    handleMcpOAuthOpen,
    handleMcpPresetAction,
    handleMcpToolsChange,
    handleMigrateModelConfigurations,
    handleZhixuFeatureAction,
    handleSaveCustomMcp,
    handleToggleProvider,
    handleWebSearchProviderChange,
    hasPendingRestart,
    hostEngineApplying,
    imageGenerationDirty,
    imageGenerationForm,
    imageGenerationSaving,
    installCapabilities,
    loading,
    localPrefs,
    mcpConfigImport,
    mcpError,
    mcpFieldValues,
    mcpMessage,
    mcpOAuthCallbackError,
    mcpOAuthCallbackUrl,
    mcpOAuthCompleting,
    mcpOAuthFlow,
    mcpOAuthPopupBlocked,
    mcpPresetAction,
    mcpPresets,
    mcpPresetsLoading,
    modelCallOrder,
    modelCallOrderSaving,
    modelConfigurationSaving,
    modelDirty,
    modelMigrationSaving,
    modelPresetBeforeCreateRef,
    modelPresetCreating,
    modelPresetEditingName,
    modelPresetNameError,
    modelPresetPendingDelete,
    zhixuFeatureAction,
    zhixuFeatureConfirm,
    zhixuFeatures,
    zhixuFeaturesError,
    zhixuFeaturesLoading,
    networkSafetyDirty,
    networkSafetyForm,
    networkSafetySaving,
    pendingRestartSections,
    providerForms,
    providerOAuthCompleting,
    providerOAuthDialogError,
    providerOAuthFlow,
    providerOAuthResponse,
    providerSaving,
    remoteBrowserAccess,
    resetWebSearchDraft,
    restartViaSettingsSurface,
    runProviderOAuth,
    saveImageGenerationSettings,
    saveModelSettings,
    saveNetworkSafetySettings,
    saveProvider,
    saveTranscriptionSettings,
    saveWebSearch,
    saving,
    selectSection,
    setAppsKindFilter,
    setAppsQuery,
    setAutomationPendingDelete,
    setAutomationPendingEdit,
    setAutomationsFilter,
    setAutomationsQuery,
    setAutomationsSort,
    setChannelsQuery,
    setCliAppsError,
    setCliAppsMessage,
    setCustomMcpForm,
    setForm,
    setImageGenerationForm,
    setLocalPrefs,
    setMcpConfigImport,
    setMcpError,
    setMcpFieldValues,
    setMcpMessage,
    setMcpOAuthCallbackError,
    setMcpOAuthCallbackUrl,
    setModelPresetCreating,
    setModelPresetEditingName,
    setModelPresetNameError,
    setModelPresetPendingDelete,
    setZhixuFeatureConfirm,
    setZhixuFeatures,
    setZhixuFeaturesError,
    setNetworkSafetyForm,
    setProviderForms,
    setProviderOAuthDialogError,
    setProviderOAuthResponse,
    setTranscriptionForm,
    setWebSearchForm,
    setWebSearchKeyEditing,
    setWebSearchKeyVisible,
    settings,
    t,
    toggleProviderKeyEditing,
    toggleProviderKeyVisibility,
    token,
    transcriptionDirty,
    transcriptionForm,
    transcriptionSaving,
    visibleProviderKeys,
    webSearchForm,
    webSearchKeyEditing,
    webSearchKeyVisible,
    webSearchSaving,
  } = controller;

  const renderSection = () => {
    if (!settings) return null;
    switch (activeSection) {
      case "overview":
        return (
          <OverviewSettings
            settings={settings}
            requiresRestart={hasPendingRestart}
            showBrandLogos={localPrefs.brandLogos}
            onSelectSection={selectSection}
          />
        );
      case "appearance":
        return (
          <AppearanceSettings
            theme={theme}
            onToggleTheme={onToggleTheme}
            localPrefs={localPrefs}
            onChangeLocalPrefs={setLocalPrefs}
          />
        );
      case "models":
        return (
          <div className="space-y-8">
            <ModelsSettings
              token={token}
              form={form}
              setForm={setForm}
              editingPresetName={modelPresetEditingName}
              presetNameError={modelPresetNameError}
              settings={settings}
              dirty={modelDirty}
              creating={modelPresetCreating}
              creatingSaving={modelConfigurationSaving}
              callOrder={modelCallOrder}
              saving={saving}
              orderSaving={modelCallOrderSaving || modelConfigurationSaving}
              migrationSaving={modelMigrationSaving}
              showBrandLogos={localPrefs.brandLogos}
              providerSaving={providerSaving}
              onChangeCallOrder={changeModelCallOrder}
              onProviderOAuthLogin={(provider) => runProviderOAuth(provider, "login")}
              onSave={saveModelSettings}
              onMigrate={handleMigrateModelConfigurations}
              onBeginCreate={beginModelPresetCreation}
              onCancelCreate={cancelModelPresetCreation}
              onClearPresetNameError={() => setModelPresetNameError(null)}
              onSelectConfiguration={(name) => {
                setModelPresetCreating(false);
                setModelPresetEditingName(name);
                setModelPresetNameError(null);
                modelPresetBeforeCreateRef.current = null;
              }}
              onDeleteConfiguration={setModelPresetPendingDelete}
            />
            <ProvidersSettings
              settings={settings}
              zhixuFeatures={zhixuFeatures}
              featureAction={zhixuFeatureAction}
              capabilityError={zhixuFeaturesError}
              expandedProvider={expandedProvider}
              providerForms={providerForms}
              visibleProviderKeys={visibleProviderKeys}
              editingProviderKeys={editingProviderKeys}
              providerSaving={providerSaving}
              showBrandLogos={localPrefs.brandLogos}
              remoteBrowserAccess={remoteBrowserAccess}
              onToggleProvider={handleToggleProvider}
              onToggleProviderKey={toggleProviderKeyVisibility}
              onToggleProviderKeyEditing={toggleProviderKeyEditing}
              onChangeProviderForm={(provider, value) =>
                setProviderForms((prev) => ({
                  ...prev,
                  [provider]: {
                    ...(prev[provider] ?? providerFormFromRow(
                      settings.providers.find((row) => row.name === provider) ?? {
                        name: provider,
                        label: provider,
                        configured: false,
                      },
                    )),
                    ...value,
                  },
                }))
              }
              onSaveProvider={saveProvider}
              onCreateCustomProvider={createCustomProvider}
              onProviderOAuthLogin={(provider) => runProviderOAuth(provider, "login")}
              onProviderOAuthLogout={(provider) => runProviderOAuth(provider, "logout")}
              imageProviderRestartPending={pendingRestartSections.image || pendingRestartSections.runtime}
              onRestart={restartViaSettingsSurface}
              isRestarting={isRestarting || hostEngineApplying}
            />
          </div>
        );
      case "image":
        return (
          <ImageGenerationSettings
            token={token}
            settings={settings}
            form={imageGenerationForm}
            dirty={imageGenerationDirty}
            saving={imageGenerationSaving}
            onChangeForm={setImageGenerationForm}
            onSave={saveImageGenerationSettings}
            onOpenProviders={() => selectSection("models")}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.image}
          />
        );
      case "voice":
        return (
          <TranscriptionSettings
            settings={settings}
            form={transcriptionForm}
            dirty={transcriptionDirty}
            saving={transcriptionSaving}
            onChangeForm={setTranscriptionForm}
            onSave={saveTranscriptionSettings}
            onOpenProviders={() => selectSection("models")}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.browser}
          />
        );
      case "browser":
        return (
          <WebSettings
            settings={settings}
            form={webSearchForm}
            keyVisible={webSearchKeyVisible}
            keyEditing={webSearchKeyEditing}
            saving={webSearchSaving}
            onChangeForm={setWebSearchForm}
            onChangeProvider={handleWebSearchProviderChange}
            onToggleKey={() => setWebSearchKeyVisible((visible) => !visible)}
            onToggleKeyEditing={() => {
              setWebSearchKeyEditing((editing) => !editing);
              setWebSearchKeyVisible(false);
              setWebSearchForm((prev) => ({ ...prev, apiKey: "" }));
            }}
            onReset={resetWebSearchDraft}
            onSave={saveWebSearch}
            showBrandLogos={localPrefs.brandLogos}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.browser}
            olostepFeature={featureCatalog.find((feature) => feature.name === "olostep")}
            olostepInstalling={zhixuFeatureAction === "enable:olostep"}
            capabilityError={zhixuFeaturesError}
          />
        );
      case "channels":
        return (
          <ChannelsSettings
            token={token}
            zhixuFeatures={zhixuFeatures}
            loading={zhixuFeaturesLoading}
            query={channelsQuery}
            actionKey={zhixuFeatureAction}
            chatAppsDocsUrl={settings.docs?.chat_apps_url}
            showBrandLogos={localPrefs.brandLogos}
            error={zhixuFeaturesError}
            requiresRestartPending={pendingRestartSections.runtime}
            onQueryChange={setChannelsQuery}
            onAction={handleZhixuFeatureAction}
            onFeaturesUpdate={setZhixuFeatures}
            onDismissStatus={() => {
              setZhixuFeaturesError(null);
            }}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
          />
        );
      case "apps":
        return (
          <AppsCatalogSettings
            cliApps={cliApps}
            mcpPresets={mcpPresets}
            cliAppsLoading={cliAppsLoading}
            mcpPresetsLoading={mcpPresetsLoading}
            query={appsQuery}
            filter={appsKindFilter}
            cliActionKey={cliAppsAction}
            mcpActionKey={mcpPresetAction}
            mcpOAuthFlow={mcpOAuthFlow}
            mcpOAuthPopupBlocked={mcpOAuthPopupBlocked}
            mcpOAuthCallbackUrl={mcpOAuthCallbackUrl}
            mcpOAuthCompleting={mcpOAuthCompleting}
            mcpOAuthCallbackError={mcpOAuthCallbackError}
            cliMessage={cliAppsMessage}
            cliError={cliAppsError}
            cliFocusName={cliAppsFocusName}
            mcpMessage={mcpMessage}
            mcpError={mcpError}
            mcpFieldValues={mcpFieldValues}
            customMcpForm={customMcpForm}
            mcpConfigImport={mcpConfigImport}
            showBrandLogos={localPrefs.brandLogos}
            requiresRestartPending={pendingRestartSections.runtime}
            onQueryChange={setAppsQuery}
            onFilterChange={setAppsKindFilter}
            onCliAction={handleCliAppAction}
            onMcpAction={handleMcpPresetAction}
            onMcpOAuthConnect={handleMcpOAuthConnect}
            onMcpOAuthCancel={() => void handleMcpOAuthCancel()}
            onMcpOAuthOpen={handleMcpOAuthOpen}
            onMcpOAuthCallbackUrlChange={(value) => {
              setMcpOAuthCallbackUrl(value);
              setMcpOAuthCallbackError(null);
            }}
            onMcpOAuthComplete={() => void handleMcpOAuthComplete()}
            onDismissStatus={() => {
              setCliAppsMessage(null);
              setCliAppsError(null);
              setMcpMessage(null);
              setMcpError(null);
            }}
            onBackToChat={onBackToChat}
            onMcpFieldChange={(presetName, fieldName, value) => {
              setMcpFieldValues((prev) => ({
                ...prev,
                [presetName]: {
                  ...(prev[presetName] ?? {}),
                  [fieldName]: value,
                },
              }));
            }}
            onCustomMcpFormChange={setCustomMcpForm}
            onMcpConfigImportChange={setMcpConfigImport}
            onSaveCustomMcp={handleSaveCustomMcp}
            onImportMcpConfig={handleImportMcpConfig}
            onMcpToolsChange={handleMcpToolsChange}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
          />
        );
      case "automations":
        return (
          <AutomationsSettings
            payload={automations}
            loading={automationsLoading}
            query={automationsQuery}
            filter={automationsFilter}
            sort={automationsSort}
            actionKey={automationAction}
            error={automationsError}
            onQueryChange={setAutomationsQuery}
            onFilterChange={setAutomationsFilter}
            onSortChange={setAutomationsSort}
            onAction={handleAutomationAction}
            onRequestEdit={setAutomationPendingEdit}
            onRequestDelete={setAutomationPendingDelete}
            onBackToChat={onBackToChat}
          />
        );
      case "skills":
        return <SkillsCatalogSettings skills={skills} />;
      case "agents":
        return <AgentsView />;
      case "teams":
        return <TeamsView />;
      case "runtime":
        return (
          <RuntimeSettings
            form={form}
            settings={settings}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.runtime}
            apiService={apiService}
            apiServiceLoading={apiServiceLoading}
            apiServiceAction={apiServiceAction}
            apiServiceError={apiServiceError}
            langfuseFeature={featureCatalog.find((feature) => feature.name === "langfuse")}
            capabilitiesLoading={zhixuFeaturesLoading}
            capabilityAction={zhixuFeatureAction}
            capabilityError={zhixuFeaturesError}
            onApiServiceAction={handleApiServiceAction}
            onInstallCapability={(name) => void installCapabilities([name])}
          />
        );
      case "advanced":
        return (
          <AdvancedSettings
            form={networkSafetyForm}
            dirty={networkSafetyDirty}
            saving={networkSafetySaving}
            isNativeHostSurface={(settings.surface ?? settings.runtime_surface) === "native"}
            onChangeForm={setNetworkSafetyForm}
            onSave={saveNetworkSafetySettings}
            onRestart={restartViaSettingsSurface}
            isRestarting={isRestarting || hostEngineApplying}
            requiresRestartPending={pendingRestartSections.runtime}
          />
        );
      default:
        return null;
    }
  };

  const standaloneCopy = showSidebar ? undefined : STANDALONE_PAGE_COPY[activeSection];

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-settings-canvas lg:flex-row">
      {showSidebar ? (
        <SettingsSidebar
          activeSection={activeSection}
          onSelectSection={selectSection}
          onBackToChat={onBackToChat}
          onLogout={onLogout}
          hostChromeInset={hostChromeInset}
        />
      ) : null}

      <ModelPresetDeleteDialog
        preset={modelPresetPendingDelete}
        deleting={saving}
        onOpenChange={(open) => {
          if (!open) setModelPresetPendingDelete(null);
        }}
        onConfirm={handleDeleteModelConfiguration}
      />

      <ProviderOAuthLoginDialog
        flow={providerOAuthFlow}
        providerLabel={
          providerOAuthFlow
            ? settings?.providers.find((provider) => provider.name === providerOAuthFlow.provider)
              ?.label ?? providerOAuthFlow.provider
            : ""
        }
        authorizationResponse={providerOAuthResponse}
        completing={providerOAuthCompleting}
        error={providerOAuthDialogError}
        remoteBrowserAccess={remoteBrowserAccess}
        onAuthorizationResponseChange={(value) => {
          setProviderOAuthResponse(value);
          setProviderOAuthDialogError(null);
        }}
        onOpenAuthorization={() => {
          if (!providerOAuthFlow) return;
          const opened = window.open(
            providerOAuthFlow.authorization_url,
            "_blank",
            "noopener,noreferrer",
          );
          if (opened) opened.opener = null;
        }}
        onComplete={() => void completeProviderOAuthResponse()}
        onClose={closeProviderOAuthFlow}
      />

      <ZhixuFeatureInstallDialog
        feature={zhixuFeatureConfirm}
        installing={zhixuFeatureAction === `enable:${zhixuFeatureConfirm?.name ?? ""}`}
        onOpenChange={(open) => {
          if (!open) setZhixuFeatureConfirm(null);
        }}
        onConfirm={(feature) => handleZhixuFeatureAction("enable", feature.name, true)}
      />

      <AutomationDeleteDialog
        job={automationPendingDelete}
        deleting={automationAction === `delete:${automationPendingDelete?.id ?? ""}`}
        onOpenChange={(open) => {
          if (!open) setAutomationPendingDelete(null);
        }}
        onConfirm={(job) => handleAutomationAction("delete", job)}
      />

      <AutomationEditDialog
        job={automationPendingEdit}
        saving={automationAction === `update:${automationPendingEdit?.id ?? ""}`}
        onOpenChange={(open) => {
          if (!open) setAutomationPendingEdit(null);
        }}
        onSave={handleAutomationEdit}
      />

      <div
        className={cn(
          "min-w-0 flex-1 bg-settings-canvas [scrollbar-gutter:stable]",
          activeSection === "channels" ||
            activeSection === "agents"
            ? "overflow-y-auto xl:overflow-hidden"
            : "overflow-y-auto",
        )}
      >
        <div
          key={activeSection}
          data-testid="settings-section-transition"
          data-settings-section={activeSection}
          className={cn(
            "mx-auto w-full animate-in fade-in-0 slide-in-from-bottom-1 duration-200 ease-out",
            "motion-reduce:animate-none",
            showSidebar ? "px-4 py-6 sm:px-8 sm:py-8 lg:py-12" : "px-4 py-5 sm:px-7 sm:py-6 lg:px-8",
            activeSection === "channels" || activeSection === "agents"
              ? "max-w-[1240px] xl:px-10"
              : showSidebar
                ? "max-w-[920px]"
                : "max-w-[1120px]",
            (activeSection === "channels" ||
              activeSection === "agents") &&
              "flex min-h-full flex-col xl:h-full xl:min-h-0",
            hostChromeInset && "pt-[4.25rem] sm:pt-[4.25rem] lg:pt-[4.75rem]",
          )}
        >
          {!showSidebar ? (
            <div className="mb-5">
              <button
                type="button"
                onClick={onBackToChat}
                className="touch-target mb-3 inline-flex items-center gap-1.5 rounded-control px-2 py-1 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground lg:hidden"
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
                {t("settings.backToChat")}
              </button>
              <header className="assistant-page-header">
                <div className="assistant-page-heading-group">
                  <h1 className="assistant-page-heading">
                    {t(`settings.nav.${activeSection}`, {
                      defaultValue: standaloneSectionTitle(activeSection),
                    })}
                  </h1>
                  {standaloneCopy ? (
                    <p className="assistant-page-description">
                      {zh ? standaloneCopy.zh : standaloneCopy.en}
                    </p>
                  ) : null}
                </div>
                {standaloneCopy ? (
                  <div
                    className="assistant-page-illustration"
                    data-size={standaloneCopy.size}
                    aria-hidden
                  >
                    <img
                      src={standaloneCopy.illustration}
                      alt=""
                      width={176}
                      height={150}
                      draggable={false}
                    />
                  </div>
                ) : null}
              </header>
            </div>
          ) : null}

          {loading ? (
            <div className="flex h-48 items-center justify-center rounded-panel bg-settings-surface text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("settings.status.loading")}
            </div>
          ) : error && !settings ? (
            <SettingsGroup>
              <SettingsRow title={t("settings.status.loadError")}>
                <span className="max-w-[520px] text-sm text-muted-foreground">{error}</span>
              </SettingsRow>
            </SettingsGroup>
          ) : settings ? (
            <div
              className={cn(
                "space-y-5",
                (activeSection === "channels" ||
                  activeSection === "agents") &&
                  "flex min-h-0 flex-1 flex-col xl:overflow-hidden",
              )}
            >
              {error ? (
                <div className="rounded-floating border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
                  {error}
                </div>
              ) : null}
              {renderSection()}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
