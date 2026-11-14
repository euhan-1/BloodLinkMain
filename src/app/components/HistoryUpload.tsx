import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle, RefreshCw, Upload } from "lucide-react";
import {
  previewHistoricalUpload, uploadHistoricalInventorySnapshots,
  type ColumnCandidate, type ColumnChoices, type ColumnRole, type DateOrder, type HistoricalUploadResult, type HistoryPreview,
} from "../lib/api";
import {
  COLUMN_QUESTIONS, COLUMN_ROLE_ORDER, SUPPORTED_FILES_LINE, countsSentence, samplesText, dateChoices, dateSentence, layoutSentence, moreProblemsLine, problemLine, repeatedSentence,
  sampleDate, saveState, savedSentence, sheetSentence,
} from "../lib/historyUpload";

// The historical stock upload: choose a file, see how it was read, then save. Nothing is saved until "Save to my history".
// The server does the reading (POST /forecast/historical-upload/preview); the save re-reads the same file the same way.

const BUTTON = "h-10 px-4 rounded-md text-[14px] font-semibold transition-colors inline-flex items-center justify-center gap-1.5";

export function HistoryUpload({ hasRealHistory, onSaved }: { hasRealHistory: boolean; onSaved: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const chooseRef = useRef<HTMLButtonElement>(null);
  const readGeneration = useRef(0);   // a preview still on its way when the user cancels must not bring the screen back
  const focusChooseNext = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<HistoryPreview | null>(null);
  const [order, setOrder] = useState<DateOrder | null>(null);
  const [example, setExample] = useState<HistoryPreview["ambiguous_example"]>(null);
  const [columns, setColumns] = useState<ColumnChoices>({});
  const [questions, setQuestions] = useState<Partial<Record<ColumnRole, ColumnCandidate[]>>>({}); // kept once asked, so the answer can be changed
  const [busy, setBusy] = useState<"reading" | "saving" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<HistoricalUploadResult | null>(null);

  // Keyboard and screen reader users land on the preview when it appears, and on the outcome after saving.
  useEffect(() => { if (preview) headingRef.current?.focus(); }, [preview === null]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (result) resultRef.current?.focus(); }, [result]);
  // After Cancel the preview is gone: focus goes to the upload button, where the user started.
  useEffect(() => { if (preview === null && focusChooseNext.current) { focusChooseNext.current = false; chooseRef.current?.focus(); } }, [preview]);

  function reset() {
    setFile(null); setPreview(null); setOrder(null); setExample(null); setColumns({}); setQuestions({}); setError(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  // Leaves the preview step as if no file had been chosen: the preview, the file and every column or date choice are dropped.
  // Never calls the server (nothing is saved, nothing already saved is touched); not possible while a save is running.
  function cancel() {
    if (busy === "saving") return;
    readGeneration.current += 1;
    setBusy(null);
    reset();
    focusChooseNext.current = true;
  }

  async function read(f: File, choice: DateOrder | null, cols: ColumnChoices) {
    const mine = ++readGeneration.current;
    setBusy("reading");
    setError(null);
    try {
      const p = await previewHistoricalUpload(f, choice, cols);
      if (mine !== readGeneration.current) return;
      if (p.ambiguous_dates && p.ambiguous_example) setExample(p.ambiguous_example);
      if (p.needs_column_choice) setQuestions((q) => ({ ...q, ...p.column_choices }));
      setPreview(p);
    } catch (err) {
      if (mine !== readGeneration.current) return;
      setError(err instanceof Error ? err.message : "We could not read that file.");
      setPreview(null);
    } finally {
      if (mine === readGeneration.current) setBusy(null);
    }
  }

  function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setResult(null);
    setOrder(null);
    setExample(null);
    setColumns({});
    setQuestions({});
    setFile(f);
    read(f, null, {});
  }

  function choose(value: DateOrder) {
    setOrder(value);
    if (file) read(file, value, columns);
  }

  function chooseColumn(role: ColumnRole, name: string) {
    const next = { ...columns, [role]: name };
    setColumns(next);
    if (file) read(file, order, next);
  }

  async function save() {
    if (!file || !preview) return;
    setBusy("saving");
    setError(null);
    try {
      setResult(await uploadHistoricalInventorySnapshots(file, order, columns));
      reset();
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The file could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  const needsChoice = preview !== null && preview.ambiguous_dates && (order === null || busy === "reading");
  const needsColumns = preview !== null && preview.needs_column_choice;
  const asksForChoice = preview !== null && !needsColumns && (preview.ambiguous_dates || order !== null);
  const hideDetails = needsChoice || needsColumns;
  const askedRoles = COLUMN_ROLE_ORDER.filter((r) => questions[r]);
  const state = preview ? saveState(preview, needsChoice, needsColumns) : null;

  return (
    <div>
      <p className="text-[14px] text-foreground mt-1">
        Have past stock records? Upload them to build the forecast from your own history sooner. Dates must be before today;
        today's stock is recorded automatically.
      </p>
      <p className="text-[14px] text-foreground mt-1">{SUPPORTED_FILES_LINE}</p>

      <input ref={fileRef} type="file" accept=".csv,.xlsx" onChange={onFile} className="hidden" tabIndex={-1} aria-hidden="true" />
      {preview === null && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            ref={chooseRef}
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy !== null}
            className="h-9 px-3 rounded-md border border-border bg-card text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60 inline-flex items-center gap-1.5"
          >
            <Upload size={14} aria-hidden="true" /> {busy === "reading" ? "Reading your file…" : hasRealHistory ? "Add more history" : "Choose a file"}
          </button>
        </div>
      )}
      <div role="status" className="sr-only">{busy === "reading" ? "Reading your file" : busy === "saving" ? "Saving" : ""}</div>

      {error && (
        <div role="alert" className="mt-3 text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {preview && (
        <section
          aria-labelledby="history-preview-heading"
          className="mt-4 rounded-lg border border-border bg-card p-4 space-y-4"
          onKeyDown={(e) => { if (e.key === "Escape" && busy !== "saving") { e.preventDefault(); cancel(); } }}
        >
          <div>
            <h4 id="history-preview-heading" ref={headingRef} tabIndex={-1} className="text-[15px] font-semibold text-foreground outline-none">
              We read your file like this
            </h4>
            {file && <p className="text-[13px] text-foreground mt-0.5 break-words">{file.name}</p>}
          </div>

          {preview.fatal ? (
            <p className="text-[14px] text-status-critical-text">{preview.fatal}</p>
          ) : !needsColumns && (
            <div className="space-y-1.5 text-[14px] text-foreground">
              <p>{layoutSentence(preview)}</p>
              {dateSentence(preview) && <p>{dateSentence(preview)}</p>}
              {sheetSentence(preview) && <p>{sheetSentence(preview)}</p>}
            </div>
          )}

          {!preview.fatal && askedRoles.map((role) => (
            <fieldset key={role} className="rounded-md border border-status-watch-border bg-status-watch-tint px-3 py-3">
              <legend className="px-1 text-[14px] font-semibold text-status-watch-text">{COLUMN_QUESTIONS[role]}</legend>
              <p className="text-[14px] text-status-watch-text mb-2">More than one column could be it. Pick the one to use; the others are left out.</p>
              <div className="space-y-2">
                {(questions[role] ?? []).map((c) => (
                  <label key={c.name} className="flex items-start gap-2.5 text-[14px] text-status-watch-text cursor-pointer">
                    <input
                      type="radio"
                      name={`history-column-${role}`}
                      value={c.name}
                      checked={columns[role] === c.name}
                      onChange={() => chooseColumn(role, c.name)}
                      className="mt-1 size-4 shrink-0 accent-primary"
                    />
                    <span>
                      <span className="font-semibold break-all">{c.name}</span>
                      <span className="block text-[13px] break-words">{samplesText(c.samples)}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          ))}

          {asksForChoice && !preview.fatal && (
            <fieldset className="rounded-md border border-status-watch-border bg-status-watch-tint px-3 py-3">
              <legend className="px-1 text-[14px] font-semibold text-status-watch-text">Some dates could be read two ways</legend>
              <p className="text-[14px] text-status-watch-text mb-2">How are the dates written in your file?</p>
              <div className="space-y-2">
                {dateChoices(example).map((c) => (
                  <label key={c.value} className="flex items-start gap-2.5 text-[14px] text-status-watch-text cursor-pointer">
                    <input
                      type="radio"
                      name="history-date-order"
                      value={c.value}
                      checked={order === c.value}
                      onChange={() => choose(c.value)}
                      className="mt-1 size-4 shrink-0 accent-primary"
                    />
                    <span><span className="font-semibold">{c.label}.</span> {c.detail}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {!preview.fatal && !hideDetails && (
            <div>
              <p className="text-[16px] font-semibold text-foreground">{countsSentence(preview)}</p>
              {repeatedSentence(preview) && <p className="text-[13px] text-foreground mt-1">{repeatedSentence(preview)}</p>}
            </div>
          )}

          {!preview.fatal && !hideDetails && preview.sample_rows.length > 0 && (
            <div>
              <p className="text-[13px] font-semibold text-foreground mb-1">The first {preview.sample_rows.length === 1 ? "row" : `${preview.sample_rows.length} rows`}, as they will be saved</p>
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="w-full text-[13px] text-foreground">
                  <caption className="sr-only">The first rows of your file, as they will be saved</caption>
                  <thead>
                    <tr className="text-left bg-secondary">
                      <th scope="col" className="px-3 py-1.5 font-semibold">Date</th>
                      <th scope="col" className="px-3 py-1.5 font-semibold">Blood type</th>
                      <th scope="col" className="px-3 py-1.5 font-semibold text-right">Units</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {preview.sample_rows.map((r, i) => (
                      <tr key={i}>
                        <td className="px-3 py-1 whitespace-nowrap">{sampleDate(r.snapshot_date)}</td>
                        <td className="px-3 py-1">{r.blood_type}</td>
                        <td className="px-3 py-1 text-right tabular-nums">{r.units}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {!hideDetails && preview.problems.length > 0 && !preview.fatal && (
            <div className="rounded-md border border-status-watch-border bg-status-watch-tint px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-[13px] font-semibold text-status-watch-text mb-1.5">
                <AlertTriangle size={14} aria-hidden="true" />
                {preview.row_counts.skipped === 1 ? "1 row will be skipped" : `${preview.row_counts.skipped.toLocaleString("en-US")} rows will be skipped`}
              </p>
              <ul className="space-y-1 text-[13px] text-status-watch-text">
                {preview.problems.map((e, i) => <li key={i}>{problemLine(e)}</li>)}
                {moreProblemsLine(preview) && <li>{moreProblemsLine(preview)}</li>}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              type="button"
              onClick={save}
              disabled={!state?.canSave || busy !== null}
              className={`${BUTTON} bg-primary text-white hover:bg-primary-hover disabled:opacity-60`}
            >
              {busy === "saving" ? <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Saving…</> : "Save to my history"}
            </button>
            <button
              type="button"
              onClick={cancel}
              disabled={busy === "saving"}
              className={`${BUTTON} border border-border bg-card text-foreground hover:bg-secondary disabled:opacity-60`}
            >
              Cancel
            </button>
          </div>
          <div>
            <button
              type="button"
              onClick={() => { if (fileRef.current) fileRef.current.value = ""; fileRef.current?.click(); }}
              disabled={busy === "saving"}
              className="py-1.5 text-[14px] font-semibold text-role-accent-text underline underline-offset-2 rounded-sm hover:no-underline disabled:opacity-60 disabled:no-underline"
            >
              Choose a different file
            </button>
          </div>
          {state?.why && <p className="text-[13px] text-foreground">{state.why}</p>}
        </section>
      )}

      {result && (
        <div ref={resultRef} tabIndex={-1} role="status" className="mt-3 text-[13px] bg-secondary rounded-md px-3 py-2 space-y-1 outline-none">
          <div className="flex items-center gap-1.5 font-semibold text-foreground">
            <CheckCircle size={14} className="shrink-0 text-status-safe-text" aria-hidden="true" /> {savedSentence(result.rows_processed, result.errors.length)}
          </div>
          <div className="text-foreground">Now {result.days_of_history} of {result.min_days_required} days of history on file. You can undo this upload in the list below.</div>
          {result.errors.slice(0, 20).map((e, i) => <div key={i} className="text-status-critical-text">{problemLine(e)}</div>)}
          {result.errors.length > 20 && <div className="text-foreground">and {(result.errors.length - 20).toLocaleString("en-US")} more</div>}
        </div>
      )}
    </div>
  );
}
