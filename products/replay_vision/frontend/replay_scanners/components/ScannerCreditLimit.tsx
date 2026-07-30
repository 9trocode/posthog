import { useValues } from 'kea'

import { LemonCard, LemonInput, LemonSwitch } from '@posthog/lemon-ui'

import { LemonField } from 'lib/lemon-ui/LemonField'
import { LemonLabel } from 'lib/lemon-ui/LemonLabel'

import { creditsToUsd } from '../../utils/credits'
import { replayScannerLogic } from '../replayScannerLogic'

interface Props {
    scannerId: string
}

// Seeded with headroom above the forecast rather than exactly on it, so the limit doesn't bind the moment
// normal month-to-month variance nudges usage up.
const SEED_HEADROOM_MULTIPLIER = 2

export function ScannerCreditLimit({ scannerId }: Props): JSX.Element {
    const { scannerEstimate } = useValues(replayScannerLogic({ id: scannerId }))
    const estimatedMonthly = scannerEstimate?.estimated_credits_per_month ?? null

    return (
        <LemonField name="monthly_credit_limit">
            {({ value, onChange }) => {
                // NaN represents "toggled on, field left empty" - distinct from null (toggle off/unlimited)
                // so an untouched field can't silently save as unlimited.
                const isEmptyButEnabled = typeof value === 'number' && Number.isNaN(value)
                const currentLimit = typeof value === 'number' && !Number.isNaN(value) ? value : null
                const limitOn = currentLimit != null || isEmptyButEnabled
                const isBelowEstimate =
                    currentLimit != null && estimatedMonthly != null && currentLimit < estimatedMonthly
                const toggleOn = (): void => {
                    // Seed from the real estimate when we have one; otherwise leave the field empty rather
                    // than invent a number the user has to notice and override.
                    onChange(
                        estimatedMonthly != null
                            ? Math.max(1, Math.round(estimatedMonthly * SEED_HEADROOM_MULTIPLIER))
                            : NaN
                    )
                }
                return (
                    <LemonCard hoverEffect={false} className="p-3 space-y-3">
                        <div className="flex items-start justify-between gap-2">
                            <div className="space-y-1">
                                <LemonLabel>Monthly credit limit</LemonLabel>
                                <div className="text-xs text-muted">
                                    Set a monthly cap so this scanner never spends past a fixed amount.
                                </div>
                            </div>
                            <LemonSwitch
                                checked={limitOn}
                                onChange={(checked) => (checked ? toggleOn() : onChange(null))}
                                data-attr="vision-scanner-credit-limit-toggle"
                            />
                        </div>
                        {limitOn && (
                            <>
                                <div className="flex items-center gap-4">
                                    <div className="w-40">
                                        <LemonInput
                                            type="number"
                                            value={isEmptyButEnabled ? NaN : (currentLimit ?? undefined)}
                                            onChange={(v) =>
                                                onChange(
                                                    v == null || Number.isNaN(v) ? NaN : Math.max(1, Math.round(v))
                                                )
                                            }
                                            min={1}
                                            step={1}
                                            suffix={<span>credits</span>}
                                        />
                                    </div>
                                    <span className="text-sm text-muted">
                                        {currentLimit != null && `≈ ${creditsToUsd(currentLimit)}/month`}
                                        {currentLimit != null && estimatedMonthly != null && ' · '}
                                        {estimatedMonthly != null &&
                                            `Estimated usage: ${creditsToUsd(estimatedMonthly)}/month`}
                                    </span>
                                </div>
                                {isBelowEstimate && (
                                    <div className="text-xs text-warning">
                                        This is below the estimated {creditsToUsd(estimatedMonthly ?? 0)}/month, so the
                                        scanner is likely to hit its limit before the period resets.
                                    </div>
                                )}
                                <div className="text-xs text-muted">
                                    When this scanner reaches its limit, it stops scanning until the next billing
                                    period. It stays enabled, but sessions it skipped this period aren't scanned later,
                                    even after you raise the limit.
                                </div>
                            </>
                        )}
                    </LemonCard>
                )
            }}
        </LemonField>
    )
}
