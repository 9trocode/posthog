import { ReplayScanner } from './types'

const DRAFT_STORAGE_KEY = 'replay-vision.new-scanner-draft'
const DRAFT_VERSION = 1
const DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

interface StoredScannerDraft {
    version: number
    teamId: number
    savedAt: number
    scanner: ReplayScanner
    /** The credit limit toggle was on with the amount still empty (a NaN sentinel JSON cannot carry). */
    creditLimitEmptyButEnabled?: boolean
}

export function writeScannerDraft(teamId: number, scanner: ReplayScanner): void {
    const draft: StoredScannerDraft = {
        version: DRAFT_VERSION,
        teamId,
        savedAt: Date.now(),
        scanner,
        creditLimitEmptyButEnabled: Number.isNaN(scanner.credit_limit as number),
    }
    try {
        localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft))
    } catch {
        // Storage unavailable or full: losing the draft only means the wizard starts fresh.
    }
}

export function readScannerDraft(teamId: number): ReplayScanner | null {
    try {
        const raw = localStorage.getItem(DRAFT_STORAGE_KEY)
        if (!raw) {
            return null
        }
        const draft: StoredScannerDraft = JSON.parse(raw)
        if (
            draft.version !== DRAFT_VERSION ||
            draft.teamId !== teamId ||
            typeof draft.savedAt !== 'number' ||
            Date.now() - draft.savedAt > DRAFT_MAX_AGE_MS ||
            !draft.scanner
        ) {
            return null
        }
        if (draft.creditLimitEmptyButEnabled) {
            return { ...draft.scanner, credit_limit: NaN }
        }
        return draft.scanner
    } catch {
        return null
    }
}

export function clearScannerDraft(): void {
    try {
        localStorage.removeItem(DRAFT_STORAGE_KEY)
    } catch {
        // Nothing to clean up if storage is unavailable.
    }
}
