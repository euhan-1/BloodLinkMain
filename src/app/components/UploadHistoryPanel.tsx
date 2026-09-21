import { useState, useEffect } from "react";
import { AlertTriangle, CheckCircle, RefreshCw, ChevronDown, History, Download, RotateCcw } from "lucide-react";
import {
  previewUploadUndo, applyUploadUndo, type UndoPreviewResult, type UndoRow, type UndoApplyResult,
  listUploadHistory, downloadUploadHistoryFile, type UploadType, type UploadHistoryEntry,
} from "../lib/api";
import { Modal } from "./Modal";

// Same row shown two ways (what will be removed, what's blocking removal) —
// one formatter so both stay in sync with whatever fields each upload type's
// eligible/blocked rows actually carry (see UndoRow in api.ts).
function formatUndoRow(uploadType: UploadType, row: UndoRow): string {
  if (uploadType === "inventory") return `${row.din} (${row.blood_type})`;
  if (uploadType === "donors") return `${row.name} — ${row.phone} (${row.blood_type})`;
  return `${row.snapshot_date} — ${row.blood_type}: ${row.units} units`;
}

// Confirmation step for "Undo this upload" — always previews fresh on open
// (never reuses a stale eligibility list) and shows exactly what would be
// removed and what's blocked, before anything actually happens.
function UndoUploadModal({ entry, onClose, onUndone }: {
  entry: UploadHistoryEntry;
  onClose: () => void;
  onUndone: () => void;
}) {
  const [preview, setPreview] = useState<UndoPreviewResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [result, setResult] = useState<UndoApplyResult | null>(null);

  useEffect(() => {
    previewUploadUndo(entry.id)
      .then(setPreview)
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load undo preview"))
      .finally(() => setLoading(false));
  }, [entry.id]);

  async function handleConfirm() {
    setApplying(true);
    setApplyError(null);
    try {
      const applied = await applyUploadUndo(entry.id);
      setResult(applied);
      onUndone();
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "Failed to undo upload");
    } finally {
      setApplying(false);
    }
  }

  return (
    <Modal title="Undo This Upload" onClose={onClose}>
      {loading && <div className="py-8 text-center text-[15px] text-muted-foreground">Checking what can be undone…</div>}
      {!loading && loadError && (
        <div className="text-[14px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
          {loadError}
        </div>
      )}

      {!loading && !loadError && preview && preview.already_undone && !result && (
        <div className="text-center py-3">
          <p className="text-[14px] text-muted-foreground mb-4">This upload has already been undone.</p>
          <button onClick={onClose} className="w-full h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors">
            Close
          </button>
        </div>
      )}

      {!loading && !loadError && preview && !preview.already_undone && !result && (
        <div className="space-y-4">
          <p className="text-[14px] text-muted-foreground">
            {entry.filename ?? "This upload"} — uploaded{" "}
            {new Date(entry.uploaded_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}.
          </p>

          {preview.eligible.length === 0 && preview.blocked.length === 0 && (
            <div className="text-[14px] text-muted-foreground">There's nothing left from this upload to remove.</div>
          )}

          {preview.eligible.length > 0 && (
            <div>
              <div className="text-[13px] font-bold text-foreground mb-1.5">
                Will remove {preview.eligible.length} record{preview.eligible.length === 1 ? "" : "s"}:
              </div>
              <div className="max-h-40 overflow-y-auto border border-border rounded-lg divide-y divide-border">
                {preview.eligible.map((row, i) => (
                  <div key={i} className="px-3 py-1.5 text-[13px] font-mono text-foreground">
                    {formatUndoRow(preview.upload_type, row)}
                  </div>
                ))}
              </div>
            </div>
          )}

          {preview.blocked.length > 0 && (
            <div className="rounded-lg border border-status-watch-border bg-status-watch-tint px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[13px] font-bold text-status-watch-text mb-1.5">
                <AlertTriangle size={13} /> {preview.blocked.length} record{preview.blocked.length === 1 ? "" : "s"} can't be removed
              </div>
              <div className="space-y-1">
                {preview.blocked.map((row, i) => (
                  <div key={i} className="text-[13px] text-status-watch-text">
                    {formatUndoRow(preview.upload_type, row)} — {row.reason}
                  </div>
                ))}
              </div>
            </div>
          )}

          {applyError && (
            <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-md px-3 py-2">
              {applyError}
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={applying}
              className="flex-1 h-10 bg-status-critical-text text-white text-sm font-bold rounded-md hover:opacity-90 transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
            >
              {applying ? (
                <><RefreshCw size={15} className="animate-spin" /> Removing…</>
              ) : preview.eligible.length > 0 ? (
                `Confirm undo (${preview.eligible.length})`
              ) : (
                "Confirm"
              )}
            </button>
          </div>
        </div>
      )}

      {result && (
        <div className="text-center py-3">
          <div className="w-12 h-12 rounded-full bg-status-safe-tint flex items-center justify-center mx-auto mb-3">
            <CheckCircle size={22} className="text-status-safe" />
          </div>
          <div className="font-display font-bold text-foreground mb-1">
            {result.removed_count} record{result.removed_count === 1 ? "" : "s"} removed
          </div>
          {result.blocked.length > 0 && (
            <p className="text-[14px] text-status-watch-text mb-3">
              {result.blocked.length} record{result.blocked.length === 1 ? "" : "s"} couldn't be removed — see reasons above.
            </p>
          )}
          <button
            onClick={onClose}
            className="w-full h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors"
          >
            Done
          </button>
        </div>
      )}
    </Modal>
  );
}

// Shared by Inventory, Donors, and the Dashboard's historical-stock backfill
// — one component, one facility-scoped log behind all three. `refreshKey`
// lets each screen force a reload right after its own upload finishes,
// without this component needing to know how that upload happened.
// `onUndone` lets the same screen also refresh its OWN primary data (the
// units table, donor count, forecast) once an undo actually removes something.
export function UploadHistoryPanel({ uploadType, refreshKey, onUndone }: { uploadType: UploadType; refreshKey?: number; onUndone?: () => void }) {
  const [entries, setEntries] = useState<UploadHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [undoEntry, setUndoEntry] = useState<UploadHistoryEntry | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    listUploadHistory(uploadType)
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load upload history"))
      .finally(() => setLoading(false));
  }
  useEffect(() => { load(); }, [uploadType, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleDownload(entry: UploadHistoryEntry) {
    setDownloadingId(entry.id);
    setDownloadError(null);
    try {
      await downloadUploadHistoryFile(entry.id, entry.filename ?? `${entry.upload_type}-upload-${entry.id}.csv`);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Failed to download file");
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-secondary flex items-center justify-center">
            <History size={14} className="text-muted-foreground" />
          </div>
          <h3 className="font-semibold text-foreground text-[16px]">Upload History</h3>
        </div>
        <button onClick={load} className="text-[12px] font-semibold text-muted-foreground hover:text-foreground transition-colors">
          Refresh
        </button>
      </div>

      {loading && <div className="py-6 text-center text-[14px] text-muted-foreground">Loading…</div>}
      {!loading && error && <div className="py-4 text-center text-[14px] text-status-critical-text">Failed to load: {error}</div>}
      {!loading && !error && entries.length === 0 && (
        <div className="py-6 text-center text-[14px] text-muted-foreground">No uploads yet.</div>
      )}
      {!loading && !error && entries.length > 0 && (
        <div className="space-y-2">
          {entries.map((entry) => {
            const expanded = expandedId === entry.id;
            const hasErrors = entry.rows_failed > 0;
            const isUndone = entry.undone_at !== null;
            return (
              <div key={entry.id} className="border border-border rounded-lg overflow-hidden">
                <button
                  onClick={() => setExpandedId(expanded ? null : entry.id)}
                  className="w-full px-3 py-2.5 text-left hover:bg-secondary transition-colors"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[13.5px] font-semibold text-foreground truncate min-w-0">
                      {entry.filename ?? "Untitled upload"}
                    </div>
                    <ChevronDown size={14} className={`text-muted-foreground transition-transform shrink-0 ${expanded ? "rotate-180" : ""}`} />
                  </div>
                  <div className="text-[12px] text-muted-foreground mt-0.5 truncate">
                    {new Date(entry.uploaded_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}
                    {entry.uploaded_by_email && ` · ${entry.uploaded_by_email}`}
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap mt-1.5">
                    {isUndone && (
                      <span className="text-[12px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap bg-secondary text-muted-foreground border-border">
                        Undone {new Date(entry.undone_at!).toLocaleDateString([], { dateStyle: "medium" })}
                      </span>
                    )}
                    <span
                      className={`text-[12px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap ${
                        hasErrors
                          ? "bg-status-watch-tint text-status-watch-text border-status-watch-border"
                          : "bg-status-safe-tint text-status-safe-text border-status-safe-border"
                      }`}
                    >
                      {entry.rows_processed} processed{hasErrors ? `, ${entry.rows_failed} failed` : ""}
                    </span>
                  </div>
                </button>
                {expanded && (
                  <div className="px-3 py-2.5 border-t border-border bg-secondary space-y-2">
                    {entry.has_raw_content ? (
                      <button
                        onClick={() => handleDownload(entry)}
                        disabled={downloadingId === entry.id}
                        className="flex items-center gap-1.5 text-[12px] font-semibold text-primary hover:underline hover:underline-offset-2 disabled:opacity-60"
                      >
                        <Download size={12} /> {downloadingId === entry.id ? "Downloading…" : "Download original file"}
                      </button>
                    ) : (
                      <div className="text-[12px] text-muted-foreground">
                        The original file isn't stored for this upload type.
                      </div>
                    )}
                    {downloadError && <div className="text-[12px] text-status-critical-text">{downloadError}</div>}
                    {entry.error_details.length > 0 && (
                      <div className="space-y-1">
                        <div className="text-[12px] font-semibold text-foreground">Rows that failed:</div>
                        {entry.error_details.map((e, i) => (
                          <div key={i} className="text-[12px] text-status-critical-text">
                            Row {e.row}: {e.reason}
                          </div>
                        ))}
                      </div>
                    )}
                    {!isUndone && (
                      <button
                        onClick={() => setUndoEntry(entry)}
                        className="flex items-center gap-1.5 text-[12px] font-semibold text-status-critical-text hover:underline hover:underline-offset-2"
                      >
                        <RotateCcw size={12} /> Undo this upload
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {undoEntry && (
        <UndoUploadModal
          entry={undoEntry}
          onClose={() => setUndoEntry(null)}
          onUndone={() => { load(); onUndone?.(); }}
        />
      )}
    </div>
  );
}
