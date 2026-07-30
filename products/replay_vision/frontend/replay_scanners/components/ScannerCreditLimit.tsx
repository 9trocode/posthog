import { useValues } from 'kea'

import { LemonCard, LemonInput, LemonSwitch } from '@posthog/lemon-ui'

import { LemonField } from 'lib/lemon-ui/LemonField'
import { LemonLabel } from 'lib/lemon-ui/LemonLabel'

import { creditsToUsd } from '../../utils/credits'
import { replayScannerLogic } from '../replayScannerLogic'

interface Props {
    scannerId: string
}

export function ScannerCreditLimit({ scannerId }: Props): JSX.Element {
    const { creditLimitState } = useValues(replayScannerLogic({ id: scannerId }))
    const {
        limit,
        isEmptyButEnabled,
        isOn,
        estimatedMonthly,
        creditsPerObservation,
        isBelowEstimate,
        cannotAffordOneScan,
        seedValue,
    } = creditLimitState

    return (
        <LemonField name="credit_limit">
            {({ onChange }) => (
                <LemonCard hoverEffect={false} className="p-3 space-y-3">
                    <div className="flex items-start justify-between gap-2">
                        <div className="space-y-1">
                            <LemonLabel>Credit limit</LemonLabel>
                            <div className="text-xs text-muted">
                                Cap what this scanner spends in a billing period, on top of your organization's limit.
                            </div>
                        </div>
                        <LemonSwitch
                            checked={isOn}
                            onChange={(checked) => onChange(checked ? seedValue : null)}
                            data-attr="vision-scanner-credit-limit-toggle"
                        />
                    </div>
                    {isOn && (
                        <>
                            <div className="flex items-center gap-4">
                                <div className="w-40">
                                    <LemonInput
                                        type="number"
                                        value={isEmptyButEnabled ? NaN : (limit ?? undefined)}
                                        onChange={(v) =>
                                            onChange(v == null || Number.isNaN(v) ? NaN : Math.max(1, Math.round(v)))
                                        }
                                        min={1}
                                        step={1}
                                        suffix={<span>credits</span>}
                                    />
                                </div>
                                <span className="text-sm text-muted">
                                    {limit != null && `≈ ${creditsToUsd(limit)} per period`}
                                    {limit != null && estimatedMonthly != null && ' · '}
                                    {estimatedMonthly != null &&
                                        `Estimated usage: ${creditsToUsd(estimatedMonthly)} a month`}
                                </span>
                            </div>
                            {cannotAffordOneScan ? (
                                <div className="text-xs text-warning">
                                    One scan by this scanner costs {creditsPerObservation} credits, so this limit stops
                                    it before it scans anything. Raise it to at least {creditsPerObservation} credits.
                                </div>
                            ) : (
                                isBelowEstimate && (
                                    <div className="text-xs text-warning">
                                        This is below the estimated {creditsToUsd(estimatedMonthly ?? 0)} a month, so
                                        the scanner is likely to stop before the period resets.
                                    </div>
                                )
                            )}
                            <div className="text-xs text-muted">
                                When this scanner reaches its limit, it stops scanning until the next billing period. It
                                stays enabled, but sessions it skipped this period aren't scanned later, even after you
                                raise the limit.
                            </div>
                        </>
                    )}
                </LemonCard>
            )}
        </LemonField>
    )
}
