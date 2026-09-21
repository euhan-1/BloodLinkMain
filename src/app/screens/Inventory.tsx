import { useState, useRef, useEffect } from "react";
import { Plus, Upload, ChevronDown, SlidersHorizontal, Trash2, Search, Archive } from "lucide-react";
import { apiGet, apiUploadFile, updateThreshold, deleteInventoryUnit, createInventoryUnit, archiveExpiredInventory, type ThresholdRow } from "../lib/api";
import { BLOOD_TYPE_ORDER, getExpiryStatus, EXPIRY_STYLES } from "../lib/statusTokens";
import { BloodTypeBadge, DinLabel, DateStamp } from "../components/BloodTypeBadge";
import { Skeleton } from "../components/Skeleton";
import { Modal } from "../components/Modal";
import { UploadHistoryPanel } from "../components/UploadHistoryPanel";
import { type InventoryUnit, type InventoryApiRow, toInventoryUnit } from "../lib/inventoryTypes";

// ─── Inventory ────────────────────────────────────────────────────────────────

type InventoryUploadResult = { rows_processed: number; errors: { row: number | string; reason: string }[] };

export function InventoryScreen() {
  const [rows, setRows] = useState<InventoryUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterType, setFilterType] = useState("All");
  const [searchQuery, setSearchQuery] = useState("");
  // Every blood-type shelf starts collapsed — with real stock levels this
  // screen is a lot of rows; better to land on a scannable summary and let
  // staff open only the type(s) they actually came to look at.
  const [collapsedTypes, setCollapsedTypes] = useState<Set<string>>(() => new Set(BLOOD_TYPE_ORDER));

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<InventoryUploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadHistoryRefreshKey, setUploadHistoryRefreshKey] = useState(0);

  // Minimum/maximum safe-stock levels per type — still global across
  // facilities, not a per-facility policy (see /inventory/summary
  // server-side), but editable here so a facility isn't stuck with the
  // seeded placeholder values forever.
  const [thresholds, setThresholds] = useState<Record<string, ThresholdRow>>({});
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
    apiGet<ThresholdRow[]>("/inventory/summary").then((data) => {
      if (cancelled) return;
      setThresholds(Object.fromEntries(data.map((t) => [t.blood_type, t])));
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
      setThresholds((prev) => ({ ...prev, [updated.blood_type]: updated }));
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
      await archiveExpiredInventory();
      loadInventory();
    } catch (err) {
      setArchiveError(err instanceof Error ? err.message : "Failed to clear expired backlog");
    } finally {
      setArchivingExpired(false);
    }
  }

  const types = ["All", ...Array.from(new Set(rows.map((r) => r.type)))];

  const byType = filterType === "All"
    ? rows
    : rows.filter((r) => r.type === filterType);

  const query = searchQuery.trim().toLowerCase();
  const filtered = query === ""
    ? byType
    : byType.filter((r) =>
        r.din.toLowerCase().includes(query) ||
        r.location.toLowerCase().includes(query) ||
        r.component.toLowerCase().includes(query)
      );

  // One "shelf" per blood type present in the filtered data, canonical order,
  // rows within a shelf sorted soonest-to-expire first — mirrors the FEFO
  // (first-expired-first-out) logic the backend already uses for real
  // transfers, so the on-screen order matches how units would actually be
  // pulled.
  const groups = BLOOD_TYPE_ORDER
    .map((type) => ({
      type,
      units: filtered.filter((r) => r.type === type).sort((a, b) => a.daysLeft - b.daysLeft),
    }))
    .filter((g) => g.units.length > 0);

  function toggleGroup(type: string) {
    setCollapsedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  return (
    <div className="max-w-screen-2xl mx-auto px-6 py-6 space-y-5">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h2 className="font-display text-lg font-bold text-foreground">Blood Inventory</h2>
          <p className="text-[14px] text-muted-foreground">
            {rows.length} units on record
            {expiredUnits.length > 0 && (
              <>
                {" · "}
                <span className="text-status-critical-text font-semibold">
                  {expiredUnits.length} expired — needs clearing
                </span>
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {expiredUnits.length > 0 && (
            <button
              onClick={handleArchiveExpired}
              disabled={archivingExpired}
              title="Archives every expired unit on file — soft-delete only, nothing is permanently removed."
              className="flex items-center gap-1.5 h-8 px-3 bg-card border border-status-critical-border text-status-critical-text rounded-lg text-[13px] font-semibold hover:bg-status-critical-tint transition-colors disabled:opacity-60"
            >
              <Archive size={14} />
              {archivingExpired ? "Clearing…" : `Clear Expired Backlog (${expiredUnits.length})`}
            </button>
          )}
          <div className="flex gap-1 bg-secondary rounded-lg p-1">
            {types.map((t) => (
              <button
                key={t}
                onClick={() => setFilterType(t)}
                className={`px-3 py-1 text-[13px] font-semibold rounded border transition-all duration-200 ease-out ${
                  filterType === t
                    ? "bg-card text-foreground shadow-sm border-border scale-105"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
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
            <Plus size={14} /> Add Unit
          </button>
        </div>
      </div>

      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search by DIN, location, or component…"
          className="w-full h-9 pl-9 pr-3 text-[14px] border border-border rounded-lg bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
        />
      </div>

      {archiveError && (
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

      {/* Shelves — one collapsible group per blood type */}
      {loading && (
        <div className="space-y-5" aria-label="Loading inventory">
          {[0, 1, 2].map((i) => (
            <div key={i} className="bg-card border border-border rounded-xl overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3 bg-secondary">
                <Skeleton className="w-14 h-14 rounded-lg shrink-0" />
                <Skeleton className="h-4 w-24" />
              </div>
              <div className="px-4 py-3 space-y-3">
                {[0, 1].map((j) => (
                  <div key={j} className="flex items-center gap-4">
                    <Skeleton className="h-4 flex-1" />
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-4 w-20" />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {!loading && error && (
        <div className="bg-status-critical-tint border border-status-critical-border rounded-xl p-6 text-center text-[14px] text-status-critical-text">
          Failed to load inventory: {error}
        </div>
      )}

      {!loading && !error && groups.length === 0 && (
        <div className="bg-card border border-border rounded-xl p-12 text-center text-[14px] text-muted-foreground">
          No units on record{filterType !== "All" ? ` for ${filterType}` : ""}{query !== "" ? ` matching "${searchQuery.trim()}"` : ""}.
        </div>
      )}

      {!loading && !error && groups.map((group) => {
        // While searching, a shelf with a match stays expanded regardless of
        // its manually-collapsed state — the point of searching is to see
        // the result immediately, not hunt for which shelf to reopen.
        const isCollapsed = query === "" && collapsedTypes.has(group.type);
        return (
          <div key={group.type} className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-secondary hover:bg-muted transition-colors">
              <button
                onClick={() => toggleGroup(group.type)}
                className="flex items-center gap-3 flex-1 min-w-0 text-left"
              >
                <BloodTypeBadge type={group.type} size="xl" />
                <span className="text-[16px] font-semibold text-foreground">
                  {group.units.length} unit{group.units.length === 1 ? "" : "s"}
                </span>
                {thresholds[group.type] && (
                  <span className="text-[14px] font-semibold text-foreground hidden sm:inline">
                    · Min {thresholds[group.type].minimum_units} · Max {thresholds[group.type].maximum_units}
                  </span>
                )}
              </button>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => openThresholdEditor(group.type)}
                  title="Set minimum/maximum stock thresholds"
                  className="flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-[12px] font-semibold text-foreground bg-card border border-border hover:border-primary/40 transition-colors"
                >
                  <SlidersHorizontal size={13} /> Thresholds
                </button>
                <button onClick={() => toggleGroup(group.type)} className="p-1">
                  <ChevronDown
                    size={16}
                    className={`text-muted-foreground transition-transform shrink-0 ${isCollapsed ? "" : "rotate-180"}`}
                  />
                </button>
              </div>
            </div>

            {/* Height-animated via the CSS grid 0fr/1fr trick rather than
                mounting/unmounting the table — lets the collapse/expand
                transition smoothly instead of snapping instantly, without
                measuring pixel heights in JS. */}
            <div
              className={`grid transition-[grid-template-rows] duration-200 ease-out ${
                isCollapsed ? "grid-rows-[0fr]" : "grid-rows-[1fr]"
              }`}
            >
              <div className="overflow-hidden">
                {/* table-layout: fixed keeps column widths stable while the
                    wrapper's height animates — a shelf can hold dozens of
                    rows, and letting the browser keep recomputing
                    auto-layout column widths every animation frame is what
                    makes a big table look janky mid-transition. */}
                <table className="w-full text-[14px] table-fixed">
                  <thead>
                    <tr className="border-b border-border bg-secondary">
                      {["DIN", "Component", "Location", "Volume (mL)", "Collection Date", "Expiration Date", "Status", ""].map((h) => (
                        <th
                          key={h}
                          className={`text-left py-3 px-4 text-[12px] font-bold uppercase tracking-wide text-muted-foreground ${h === "" ? "w-12" : ""}`}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {group.units.map((row) => {
                      const status = getExpiryStatus(row.daysLeft);
                      const style = EXPIRY_STYLES[status];
                      return (
                        <tr
                          key={row.din}
                          className={`border-b border-border last:border-0 hover:bg-secondary transition-colors ${style.rowTint}`}
                        >
                          <td className="py-3 px-4">
                            <DinLabel din={row.din} />
                          </td>
                          <td className="py-3 px-4 text-foreground">{row.component}</td>
                          <td className="py-3 px-4">
                            <span className="font-mono text-[13px] text-muted-foreground">{row.location}</span>
                          </td>
                          <td className="py-3 px-4 text-foreground tabular-nums">{row.volume}</td>
                          <td className="py-3 px-4 text-muted-foreground font-mono text-[13px]">{row.collected}</td>
                          <td className="py-3 px-4">
                            <DateStamp date={row.expires} status={status} />
                          </td>
                          <td className="py-3 px-4">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[12px] font-semibold border ${style.badge}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
                              {style.label(row.daysLeft)}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-center">
                            <button
                              onClick={() => { setDeleteError(null); setDeleteConfirmDin(row.din); }}
                              title="Remove this unit"
                              className="p-1.5 rounded-lg text-status-critical-text hover:bg-status-critical-tint transition-colors"
                            >
                              <Trash2 size={15} />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        );
      })}

      <UploadHistoryPanel uploadType="inventory" refreshKey={uploadHistoryRefreshKey} onUndone={() => loadInventory()} />

      {editingType && (
        <Modal title={`${editingType} — Stock Thresholds`} onClose={() => setEditingType(null)}>
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
        <Modal title="Remove Unit" onClose={() => setDeleteConfirmDin(null)}>
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
                Keep Unit
              </button>
              <button
                onClick={confirmDeleteUnit}
                disabled={deleting}
                className="flex-1 h-10 bg-status-critical-text text-white text-sm font-bold rounded-md hover:opacity-90 transition-colors disabled:opacity-60"
              >
                {deleting ? "Removing…" : "Remove Unit"}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {showAddUnit && (
        <Modal title="Add Unit" onClose={() => setShowAddUnit(false)}>
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
                {addingUnit ? "Adding…" : "Add Unit"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

