import { useValues } from 'kea'

import { LemonCard, LemonInput, LemonSwitch } from '@posthog/lemon-ui'

import { LemonField } from 'lib/lemon-ui/LemonField'
import { LemonLabel } from 'lib/lemon-ui/LemonLabel'

import { creditsToUsd } from '../../utils/credits'
import { replayScannerLogic } from '../replayScannerLogic'

interface Props {
    scannerId: string
}

// Used only when the live estimate hasn't computed yet (e.g. a brand-new scanner). Broad enough to not
// immediately bind for most scanners, while still being a real cap rather than an unlimited default.
const FALLBACK_SEED_LIMIT = 1000

// Seeded with headroom above the forecast rather than exactly on it, so the limit doesn't bind the moment
// normal month-to-month variance nudges usage up.
const SEED_HEADROOM_MULTIPLIER = 2

export function ScannerCreditLimit({ scannerId }: Props): JSX.Element {
    const { scannerEstimate } = useValues(replayScannerLogic({ id: scannerId }))
    const estimatedMonthly = scannerEstimate?.estimated_credits_per_month ?? null

    return (
        <LemonField name="monthly_credit_limit">
            {({ value, onChange }) => {
                const currentLimit = typeof value === 'number' ? value : null
                const isBelowEstimate =
                    currentLimit != null && estimatedMonthly != null && currentLimit < estimatedMonthly
                const toggleOn = (): void => {
                    const seed =
                        estimatedMonthly != null
                            ? Math.max(1, Math.round(estimatedMonthly * SEED_HEADROOM_MULTIPLIER))
                            : FALLBACK_SEED_LIMIT
                    onChange(seed)
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
                                checked={currentLimit != null}
                                onChange={(checked) => (checked ? toggleOn() : onChange(null))}
                                data-attr="vision-scanner-credit-limit-toggle"
                            />
                        </div>
                        {currentLimit != null && (
                            <>
                                <div className="flex items-center gap-4">
                                    <div className="w-40">
                                        <LemonInput
                                            type="number"
                                            value={currentLimit}
                                            onChange={(v) => onChange(Math.max(1, Math.round(Number(v) || 1)))}
                                            min={1}
                                            step={1}
                                            suffix={<span>credits</span>}
                                        />
                                    </div>
                                    <span className="text-sm text-muted">≈ {creditsToUsd(currentLimit)}/month</span>
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
