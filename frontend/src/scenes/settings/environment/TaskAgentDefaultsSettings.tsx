import { useActions, useValues } from 'kea'

import { LemonButton, LemonSelect } from '@posthog/lemon-ui'

import { modelCatalogueLogic } from 'products/posthog_ai/frontend/logics/modelCatalogueLogic'
import {
    getEffortLabel,
    getEffortsForModel,
    getModelLabel,
    getRuntimeAdapterLabel,
    listRuntimeAdapters,
    modelsForRuntimeAdapter,
} from 'products/posthog_ai/frontend/utils/composerModels'

import { type AIRunPreferenceDraft, taskAgentDefaultsLogic } from './taskAgentDefaultsLogic'

function PreferenceEditor({
    draft,
    saving,
    inheritLabel,
    onChange,
    onSave,
}: {
    draft: AIRunPreferenceDraft
    saving: boolean
    inheritLabel: string
    onChange: (draft: Partial<AIRunPreferenceDraft>) => void
    onSave: () => void
}): JSX.Element {
    const { catalogue } = useValues(modelCatalogueLogic)

    // Grouped by harness off the same catalogue the composer renders, so a model you can pick for a
    // run is always settable as a default and vice versa — including the Codex models that only
    // Slack and PostHog Code drive today.
    const modelOptions = listRuntimeAdapters(catalogue).map((adapter) => ({
        title: getRuntimeAdapterLabel(adapter),
        options: modelsForRuntimeAdapter(catalogue, adapter).map((choice) => ({
            value: choice.model,
            label: choice.display_name,
        })),
    }))
    const effortOptions = getEffortsForModel(catalogue, draft.model)

    return (
        <div className="flex flex-wrap items-center gap-2">
            <LemonSelect
                value={draft.model}
                onChange={(model) =>
                    onChange({
                        model,
                        // A model switch may invalidate the picked effort; reset to the server-side default.
                        reasoning_effort:
                            draft.reasoning_effort &&
                            model &&
                            getEffortsForModel(catalogue, model).some(
                                (option) => option.value === draft.reasoning_effort
                            )
                                ? draft.reasoning_effort
                                : null,
                    })
                }
                options={[{ options: [{ value: null as string | null, label: inheritLabel }] }, ...modelOptions]}
                placeholder={inheritLabel}
                data-attr="task-agent-default-model"
            />
            <LemonSelect
                value={draft.reasoning_effort}
                onChange={(reasoning_effort) => onChange({ reasoning_effort })}
                options={[
                    { value: null as string | null, label: 'Default effort' },
                    ...effortOptions.map(({ value, label }) => ({ value: value as string, label })),
                ]}
                disabledReason={draft.model ? undefined : 'Pick a model first'}
                data-attr="task-agent-default-effort"
            />
            <LemonButton type="primary" onClick={onSave} loading={saving}>
                Save
            </LemonButton>
        </div>
    )
}

export function TaskAgentProjectDefaultSettings(): JSX.Element {
    const { teamDraft, teamPreferencesLoading } = useValues(taskAgentDefaultsLogic)
    const { setTeamDraft, submitTeamDraft } = useActions(taskAgentDefaultsLogic)

    return (
        <PreferenceEditor
            draft={teamDraft}
            saving={teamPreferencesLoading}
            inheritLabel="No project default"
            onChange={setTeamDraft}
            onSave={submitTeamDraft}
        />
    )
}

export function TaskAgentMyPreferenceSettings(): JSX.Element {
    const { myDraft, myPreferencesLoading, resolvedDefaults } = useValues(taskAgentDefaultsLogic)
    const { catalogue } = useValues(modelCatalogueLogic)
    const { setMyDraft, submitMyDraft } = useActions(taskAgentDefaultsLogic)

    return (
        <div className="flex flex-col gap-2">
            <PreferenceEditor
                draft={myDraft}
                saving={myPreferencesLoading}
                inheritLabel="Use project default"
                onChange={setMyDraft}
                onSave={submitMyDraft}
            />
            <p className="text-secondary mb-0">
                {resolvedDefaults?.model ? (
                    <>
                        Runs you start without picking a model will use{' '}
                        <strong>{getModelLabel(catalogue, resolvedDefaults.model)}</strong>
                        {resolvedDefaults.reasoning_effort ? (
                            <> ({getEffortLabel(resolvedDefaults.reasoning_effort)} effort)</>
                        ) : null}{' '}
                        from the {resolvedDefaults.source === 'user' ? 'preference above' : 'project default'}.
                    </>
                ) : (
                    <>No default is set — runs use each surface's built-in model.</>
                )}
            </p>
        </div>
    )
}
