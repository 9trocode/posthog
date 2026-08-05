import { readScannerDraft, writeScannerDraft } from './scannerDraft'
import { newScanner } from './scannerTemplates'

const STORAGE_KEY = 'replay-vision.new-scanner-draft'

describe('scannerDraft', () => {
    beforeEach(() => {
        localStorage.clear()
    })

    it.each([
        ['returns the draft for the same team', 1, (draft: any) => draft, true],
        ['rejects a draft from another team', 2, (draft: any) => draft, false],
        [
            'rejects a stale draft',
            1,
            (draft: any) => ({ ...draft, savedAt: Date.now() - 8 * 24 * 60 * 60 * 1000 }),
            false,
        ],
        ['rejects a different schema version', 1, (draft: any) => ({ ...draft, version: 999 }), false],
        ['rejects unparseable storage', 1, () => 'not json', false],
    ])('%s', (_label, readTeamId, tamper, expectRestored) => {
        const scanner = { ...newScanner(null), name: 'Drafted scanner' }
        writeScannerDraft(1, scanner)
        const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
        const tampered = tamper(stored)
        localStorage.setItem(STORAGE_KEY, typeof tampered === 'string' ? tampered : JSON.stringify(tampered))
        expect(readScannerDraft(readTeamId)?.name ?? null).toBe(expectRestored ? 'Drafted scanner' : null)
    })

    it('keeps the credit limit toggle on across the round trip while the amount is still empty', () => {
        // JSON cannot carry the NaN sentinel; without the marker the toggle silently comes back off.
        const scanner = { ...newScanner(null), credit_limit: NaN }
        writeScannerDraft(1, scanner)
        expect(Number.isNaN(readScannerDraft(1)?.credit_limit as number)).toBe(true)
    })

    it('restores a concrete credit limit as-is', () => {
        const scanner = { ...newScanner(null), credit_limit: 500 }
        writeScannerDraft(1, scanner)
        expect(readScannerDraft(1)?.credit_limit).toBe(500)
    })
})
