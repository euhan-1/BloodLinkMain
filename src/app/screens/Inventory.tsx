import { useState, useRef, useEffect } from "react";
import { Plus, Upload, Archive } from "lucide-react";
import { apiGet, apiUploadFile, updateThreshold, deleteInventoryUnit, createInventoryUnit, archiveExpiredInventory } from "../lib/api";
import { getCurrentUser } from "../lib/session";
import { StockByTypeTable } from "./HospitalInventory";
import { BankInventoryView } from "./BankInventory";
import { BLOOD_TYPE_ORDER, getExpiryStatus } from "../lib/statusTokens";
import { Modal } from "../components/Modal";
import { UploadHistoryPanel } from "../components/UploadHistoryPanel";
import { type InventoryUnit, type InventoryApiRow, type InventorySummaryRow, toInventoryUnit } from "../lib/inventoryTypes";

// ─── Inventory ────────────────────────────────────────────────────────────────

type InventoryUploadResult = { rows_processed: number; errors: { row: number | string; reason: string }[] };

export function InventoryScreen() {
  const [rows, setRows] = useState<InventoryUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Every blood-type shelf starts collapsed — with real stock levels this
  // screen is a lot of rows; better to land on a scannable summary and let
  // staff open only the type(s) they actually came to look at.
  const [collapsedTypes, setCollapsedTypes] = useState<Set<string>>(() => new Set(BLOOD_TYPE_ORDER));

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<InventoryUploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadHistoryRefreshKey, setUploadHistoryRefreshKey] = useState(0);

  // Minimum/maximum safe-stock levels per type — per-facility (see
  // /inventory/summary server-side; blood_type_thresholds is keyed
  // (facility_id, blood_type)), and editable here so a facility isn't stuck
  // with the seeded placeholder values forever.
  const [thresholds, setThresholds] = useState<Record<string, InventorySummaryRow>>({});
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [editingType, setEditingType] = useState<string | null>(null);
  const [editMin, setEditMin] = useState("");
  const [editMax, setEditMax] = useState("");
  const [savingThreshold, setSavingThreshold] = useState(false);
  const [thresholdError, setThresholdError] = useState<string | null>(null);

  const [deleteConfirmDin, setDeleteConfirmDin] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Expired units still appear in the list below (so staff can see what
  // needs clearing) but are already excluded from every usable/available
  // count elsewhere (Dashboard totals, thresholds, forecast, emergency
  // sourcing — all filter expires_date server-side). Archiving here is
  // soft-delete only: it never removes the row, just clears it out of this
  // working list.
  const [archivingExpired, setArchivingExpired] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [lastCleared, setLastCleared] = useState<number | null>(null);

  const emptyUnitForm = {
    din: "",
    bloodType: BLOOD_TYPE_ORDER[0],
    component: "Packed RBC",
    location: "",
    volumeMl: "280",
    collectedDate: "",
    expiresDate: "",
  };
  const [showAddUnit, setShowAddUnit] = useState(false);
  const [unitForm, setUnitForm] = useState(emptyUnitForm);
  const [addingUnit, setAddingUnit] = useState(false);
  const [addUnitError, setAddUnitError] = useState<string | null>(null);

  function loadInventory(onSettled?: () => void) {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiGet<InventoryApiRow[]>("/inventory")
      .then((data) => {
        if (!cancelled) setRows(data.map(toInventoryUnit));
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load inventory");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
        onSettled?.();
      });
    return () => {
      cancelled = true;
    };
  }

  useEffect(() => loadInventory(), []);

  useEffect(() => {
    let cancelled = false;
    apiGet<InventorySummaryRow[]>("/inventory/summary")
      .then((data) => {
        if (!cancelled) setThresholds(Object.fromEntries(data.map((t) => [t.blood_type, t])));
      })
      .catch((err) => {
        if (!cancelled) setSummaryError(err instanceof Error ? err.message : "Failed to load stock levels");
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function openThresholdEditor(type: string) {
    const current = thresholds[type];
    setEditMin(current ? String(current.minimum_units) : "");
    setEditMax(current ? String(current.maximum_units) : "");
    setThresholdError(null);
    setEditingType(type);
  }

  async function saveThreshold() {
    if (!editingType) return;
    const minimum = Number(editMin);
    const maximum = Number(editMax);
    if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum < 0 || maximum < 0) {
      setThresholdError("Enter valid non-negative numbers.");
      return;
    }
    if (minimum > maximum) {
      setThresholdError("Minimum cannot be greater than maximum.");
      return;
    }
    setSavingThreshold(true);
    setThresholdError(null);
    try {
      const updated = await updateThreshold(editingType, minimum, maximum);
      setThresholds((prev) => ({ ...prev, [updated.blood_type]: { ...prev[updated.blood_type], ...updated } }));
      setEditingType(null);
    } catch (err) {
      setThresholdError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSavingThreshold(false);
    }
  }

  async function confirmDeleteUnit() {
    if (!deleteConfirmDin) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteInventoryUnit(deleteConfirmDin);
      setDeleteConfirmDin(null);
      loadInventory();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to remove unit");
    } finally {
      setDeleting(false);
    }
  }

  function openAddUnit() {
    setUnitForm(emptyUnitForm);
    setAddUnitError(null);
    setShowAddUnit(true);
  }

  async function submitAddUnit() {
    const din = unitForm.din.trim();
    const location = unitForm.location.trim();
    const volumeMl = Number(unitForm.volumeMl);
    if (!din) {
      setAddUnitError("DIN is required.");
      return;
    }
    if (!location) {
      setAddUnitError("Location is required.");
      return;
    }
    if (!Number.isFinite(volumeMl) || volumeMl <= 0) {
      setAddUnitError("Volume must be a positive number.");
      return;
    }
    if (!unitForm.collectedDate || !unitForm.expiresDate) {
      setAddUnitError("Collection and expiration dates are required.");
      return;
    }
    if (unitForm.expiresDate <= unitForm.collectedDate) {
      setAddUnitError("Expiration date must be after the collection date.");
      return;
    }
    setAddingUnit(true);
    setAddUnitError(null);
    try {
      await createInventoryUnit({
        din,
        blood_type: unitForm.bloodType,
        component: unitForm.component.trim(),
        location,
        volume_ml: volumeMl,
        collected_date: unitForm.collectedDate,
        expires_date: unitForm.expiresDate,
      });
      setShowAddUnit(false);
      loadInventory();
    } catch (err) {
      setAddUnitError(err instanceof Error ? err.message : "Failed to add unit");
    } finally {
      setAddingUnit(false);
    }
  }

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    setUploadResult(null);
    try {
      const result = await apiUploadFile<InventoryUploadResult>("/inventory/upload", file);
      setUploadResult(result);
      loadInventory();
      setUploadHistoryRefreshKey((k) => k + 1);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  const expiredUnits = rows.filter((r) => getExpiryStatus(r.daysLeft) === "expired");

  async function handleArchiveExpired() {
    setArchivingExpired(true);
    setArchiveError(null);
    try {
      const { archived_count } = await archiveExpiredInventory();
      setLastCleared(archived_count);
      loadInventory();
    } catch (err) {
      setArchiveError(err instanceof Error ? err.message : "Failed to clear expired backlog");
    } finally {
      setArchivingExpired(false);
    }
  }

  function toggleGroup(type: string) {
    setCollapsedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  // The page-level actions, shared by both layouts so they cannot drift apart.
  const clearExpiredButton = expiredUnits.length > 0 && (
            <button
              onClick={handleArchiveExpired}
              disabled={archivingExpired}
              title="Archives every expired unit on file — soft-delete only, nothing is permanently removed."
              className="flex items-center gap-1.5 h-8 px-3 bg-card border border-status-critical-border text-status-critical-text rounded-lg text-[13px] font-semibold hover:bg-status-critical-tint transition-colors disabled:opacity-60"
            >
              <Archive size={14} />
              {archivingExpired ? "Clearing…" : `Clear expired backlog (${expiredUnits.length})`}
            </button>
  );
  const importButtons = (
    <>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            onChange={handleFileSelected}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            title="Columns: din, blood_type, component, location, volume_ml, collected_date, expires_date. Re-uploading a DIN already on file at this facility updates it as a correction."
            className="flex items-center gap-1.5 h-8 px-3 bg-card border border-border rounded-lg text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors disabled:opacity-60"
          >
            <Upload size={14} /> {uploading ? "Uploading…" : "Upload CSV"}
          </button>
          <button
            onClick={openAddUnit}
            className="flex items-center gap-1.5 h-8 px-3 bg-primary text-white rounded-lg text-[13px] font-semibold hover:bg-primary-hover transition-colors"
          >
            <Plus size={14} /> Add unit
          </button>
    </>
  );

  // Hospitals get the stock-by-type table; blood banks keep the shelves below until this is rolled out to them.
  const hospital = getCurrentUser()?.facility_type === "hospital";
  const usableUnits = rows.filter((r) => r.daysLeft >= 0).length;

  return (
    <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-8">
      {(
        <div className={`${hospital ? "max-w-5xl " : ""}flex flex-wrap items-end justify-between gap-x-6 gap-y-3`}>
          <div>
            <h2 className="font-display font-bold text-2xl tracking-tight text-foreground">Blood inventory</h2>
            <p className="text-[15px] text-foreground mt-1">
              <span className="mr-4">{usableUnits} {usableUnits === 1 ? "unit" : "units"} on hand</span>
              {hospital && expiredUnits.length > 0 && (
                <span className="text-status-critical-text font-semibold">
                  {expiredUnits.length} expired {expiredUnits.length === 1 ? "unit needs" : "units need"} clearing
                </span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {hospital && clearExpiredButton}
            {importButtons}
          </div>
        </div>
      )}

      {hospital && archiveError && (
        <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
          {archiveError}
        </div>
      )}
      {uploadError && (
        <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
          {uploadError}
        </div>
      )}
      {uploadResult && (
        <div className="text-[13px] bg-card border border-border rounded-lg px-3 py-2.5 space-y-1">
          <div className="font-semibold text-foreground">
            {uploadResult.rows_processed} unit{uploadResult.rows_processed === 1 ? "" : "s"} processed
            {uploadResult.errors.length > 0 && `, ${uploadResult.errors.length} row${uploadResult.errors.length === 1 ? "" : "s"} skipped`}
          </div>
          {uploadResult.errors.map((e, i) => (
            <div key={i} className="text-status-critical-text">
              Row {e.row}: {e.reason}
            </div>
          ))}
        </div>
      )}

      {hospital && (
        <div className="max-w-5xl">
          <StockByTypeTable
            summary={thresholds}
            units={rows}
            loading={loading || summaryLoading}
            error={error ?? summaryError}
            isOpen={(t) => !collapsedTypes.has(t)}
            onToggle={toggleGroup}
            onEditThresholds={openThresholdEditor}
            onRemoveUnit={(din) => { setDeleteError(null); setDeleteConfirmDin(din); }}
          />
        </div>
      )}

      {!hospital && (
        <BankInventoryView
          units={rows}
          loading={loading}
          error={error}
          summary={thresholds}
          summaryLoading={summaryLoading}
          summaryError={summaryError}
          clearing={{ busy: archivingExpired, error: archiveError, lastCleared, onClear: handleArchiveExpired }}
          onEditThresholds={openThresholdEditor}
          onRemoveUnit={(din) => { setDeleteError(null); setDeleteConfirmDin(din); }}
        />
      )}

      <div className={hospital ? "max-w-5xl" : undefined}>
        <UploadHistoryPanel uploadType="inventory" refreshKey={uploadHistoryRefreshKey} onUndone={() => loadInventory()} variant="flat" />
      </div>

      {editingType && (
        <Modal title={`${editingType}: stock thresholds`} onClose={() => setEditingType(null)}>
          <div className="space-y-4">
            <p className="text-[13px] text-muted-foreground">
              Minimum triggers a shortage warning on the Dashboard; maximum is the target ceiling the chart scales against.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Minimum units</label>
                <input
                  type="number"
                  min={0}
                  value={editMin}
                  onChange={(e) => setEditMin(e.target.value)}
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Maximum units</label>
                <input
                  type="number"
                  min={0}
                  value={editMax}
                  onChange={(e) => setEditMax(e.target.value)}
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
            </div>

            {thresholdError && (
              <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
                {thresholdError}
              </div>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => setEditingType(null)}
                className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={saveThreshold}
                disabled={savingThreshold}
                className="flex-1 h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60"
              >
                {savingThreshold ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {deleteConfirmDin && (
        <Modal title="Remove unit" onClose={() => setDeleteConfirmDin(null)}>
          <div className="space-y-4">
            <p className="text-[14px] text-muted-foreground">
              Remove unit <span className="font-mono font-semibold text-foreground">{deleteConfirmDin}</span> from
              inventory? This can't be undone.
            </p>

            {deleteError && (
              <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
                {deleteError}
              </div>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => setDeleteConfirmDin(null)}
                className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors"
              >
                Keep unit
              </button>
              <button
                onClick={confirmDeleteUnit}
                disabled={deleting}
                className="flex-1 h-10 bg-status-critical-text text-white text-sm font-bold rounded-md hover:opacity-90 transition-colors disabled:opacity-60"
              >
                {deleting ? "Removing…" : "Remove unit"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {showAddUnit && (
        <Modal title="Add unit" onClose={() => setShowAddUnit(false)}>
          <div className="space-y-4">
            <div>
              <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">DIN</label>
              <input
                type="text"
                value={unitForm.din}
                onChange={(e) => setUnitForm((f) => ({ ...f, din: e.target.value }))}
                placeholder="e.g. BL2026-2001"
                className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
              />
            </div>

            <div>
              <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Blood type</label>
              <div className="grid grid-cols-4 gap-1.5">
                {BLOOD_TYPE_ORDER.map((bt) => (
                  <button
                    key={bt}
                    onClick={() => setUnitForm((f) => ({ ...f, bloodType: bt }))}
                    className={`py-2 text-[14px] font-display font-bold rounded-lg border transition-all duration-200 ease-out ${
                      unitForm.bloodType === bt
                        ? "bg-primary text-white border-primary scale-110"
                        : "bg-primary-tint text-primary border-transparent hover:border-primary/40"
                    }`}
                  >
                    {bt}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Component</label>
                <input
                  type="text"
                  value={unitForm.component}
                  onChange={(e) => setUnitForm((f) => ({ ...f, component: e.target.value }))}
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Location</label>
                <input
                  type="text"
                  value={unitForm.location}
                  onChange={(e) => setUnitForm((f) => ({ ...f, location: e.target.value }))}
                  placeholder="e.g. Bay A-1"
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Volume (mL)</label>
                <input
                  type="number"
                  min={1}
                  value={unitForm.volumeMl}
                  onChange={(e) => setUnitForm((f) => ({ ...f, volumeMl: e.target.value }))}
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Collected</label>
                <input
                  type="date"
                  value={unitForm.collectedDate}
                  onChange={(e) => setUnitForm((f) => ({ ...f, collectedDate: e.target.value }))}
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
              <div>
                <label className="text-[13px] font-semibold text-muted-foreground block mb-1.5">Expires</label>
                <input
                  type="date"
                  value={unitForm.expiresDate}
                  onChange={(e) => setUnitForm((f) => ({ ...f, expiresDate: e.target.value }))}
                  className="w-full h-9 px-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                />
              </div>
            </div>

            {addUnitError && (
              <div className="text-[13px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-3 py-2">
                {addUnitError}
              </div>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => setShowAddUnit(false)}
                className="flex-1 h-10 border border-border rounded-md text-sm font-semibold text-foreground hover:bg-secondary transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={submitAddUnit}
                disabled={addingUnit}
                className="flex-1 h-10 bg-primary text-white text-sm font-bold rounded-md hover:bg-primary-hover transition-colors disabled:opacity-60"
              >
                {addingUnit ? "Adding…" : "Add unit"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

