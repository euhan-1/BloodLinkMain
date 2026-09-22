import { useState, useRef, useEffect } from "react";
import { AlertTriangle, CheckCircle, Clock, MessageSquare, Phone, Send, Upload } from "lucide-react";
import { apiGet, apiPost, apiUploadFile } from "../lib/api";
import { isDevModeEnabled } from "../lib/devMode";
import { UploadHistoryPanel } from "../components/UploadHistoryPanel";
import { ALL_BLOOD_TYPES } from "../lib/statusTokens";

// ─── SMS Donor Blast ──────────────────────────────────────────────────────────

type DonorUploadResult = { rows_processed: number; errors: { row: number; reason: string }[] };

type BlastSummary = {
  id: number;
  blood_type: string;
  target_count: number;
  time_limit_hours: number;
  status: string;
  created_at: string;
  deadline_at: string;
};

type BlastMessageEntry = {
  donor_id: number;
  donor_name: string;
  phone: string;
  message_text: string;
};

type CreateBlastResponse = {
  blast: BlastSummary;
  simulated: boolean;
  label: string;
  recipient_count: number;
  messages: BlastMessageEntry[];
};

type BlastMessagesResponse = { simulated: boolean; label: string; messages: BlastMessageEntry[] };

type ConfirmedDonor = { name: string; phone: string; replied_at: string };
type ConfirmedResponse = {
  blast_status: string;
  target_count: number;
  confirmed_count: number;
  confirmed_donors: ConfirmedDonor[];
};

export function ChatScreen() {
  const isDevMode = isDevModeEnabled();

  // Donor roster upload
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<DonorUploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [donorCount, setDonorCount] = useState<number | null>(null);
  const [uploadHistoryRefreshKey, setUploadHistoryRefreshKey] = useState(0);

  function refreshDonorCount() {
    apiGet<unknown[]>("/donors").then((rows) => setDonorCount(rows.length)).catch(() => {});
  }
  useEffect(() => { refreshDonorCount(); }, []);

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    setUploadResult(null);
    try {
      const result = await apiUploadFile<DonorUploadResult>("/donors/upload", file);
      setUploadResult(result);
      refreshDonorCount();
      setUploadHistoryRefreshKey((k) => k + 1);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  // Blast creation
  const [selectedType, setSelectedType] = useState("O-");
  const [targetCount, setTargetCount] = useState(5);
  const [timeLimitHours, setTimeLimitHours] = useState(2);
  const [creatingBlast, setCreatingBlast] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Active blast detail — either just created, or the most recent one on
  // mount, so a page refresh doesn't lose visibility mid-drive.
  const [activeBlastId, setActiveBlastId] = useState<number | null>(null);
  const [blastSummary, setBlastSummary] = useState<BlastSummary | null>(null);
  const [blastMessagesData, setBlastMessagesData] = useState<BlastMessagesResponse | null>(null);
  const [confirmedData, setConfirmedData] = useState<ConfirmedResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // Tracks the latest requested blast id so an in-flight fetch for a
  // previous blast (e.g. the on-mount "resume most recent" load) can't
  // overwrite fresher data if it resolves after a newer blast was created —
  // a real race condition otherwise, since both fetches hit the same setters.
  const activeBlastIdRef = useRef<number | null>(null);
  useEffect(() => {
    activeBlastIdRef.current = activeBlastId;
  }, [activeBlastId]);

  function loadBlastDetails(blastId: number) {
    setDetailLoading(true);
    setDetailError(null);
    return Promise.all([
      apiGet<BlastMessagesResponse>(`/donors/blasts/${blastId}/messages`),
      apiGet<ConfirmedResponse>(`/donors/blasts/${blastId}/confirmed`),
    ])
      .then(([messagesRes, confirmedRes]) => {
        if (activeBlastIdRef.current !== blastId) return; // stale response, a newer blast is now active
        setBlastMessagesData(messagesRes);
        setConfirmedData(confirmedRes);
        setBlastSummary((prev) => (prev && prev.id === blastId ? { ...prev, status: confirmedRes.blast_status } : prev));
      })
      .catch((err) => {
        if (activeBlastIdRef.current === blastId) {
          setDetailError(err instanceof Error ? err.message : "Failed to load blast details");
        }
      })
      .finally(() => {
        if (activeBlastIdRef.current === blastId) setDetailLoading(false);
      });
  }

  useEffect(() => {
    apiGet<BlastSummary[]>("/donors/blasts")
      .then((blasts) => {
        if (blasts.length > 0) {
          setActiveBlastId(blasts[0].id);
          setBlastSummary(blasts[0]);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (activeBlastId !== null) loadBlastDetails(activeBlastId);
  }, [activeBlastId]);

  async function handleCreateBlast() {
    setCreatingBlast(true);
    setCreateError(null);
    try {
      const result = await apiPost<CreateBlastResponse>("/donors/blast", {
        blood_type: selectedType,
        target_count: targetCount,
        time_limit_hours: timeLimitHours,
      });
      setBlastSummary(result.blast);
      setActiveBlastId(result.blast.id);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create blast");
    } finally {
      setCreatingBlast(false);
    }
  }

  // DEV-ONLY tools (?dev=1) — the only way a reply is ever recorded, since
  // there's no real SMS provider/webhook. Mirrors ALLOW_DEV_TEST_TOOLS
  // server-side; if that's disabled the calls below will 403 with a clear message.
  const [simDonorId, setSimDonorId] = useState<number | "">("");
  const [simReply, setSimReply] = useState<"yes" | "no">("yes");
  const [simTimestamp, setSimTimestamp] = useState("");
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);

  async function handleSimulateReply() {
    if (activeBlastId === null || simDonorId === "") return;
    setSimBusy(true);
    setSimError(null);
    try {
      await apiPost(`/donors/blasts/${activeBlastId}/simulate-reply`, {
        donor_id: simDonorId,
        reply: simReply,
        replied_at: simTimestamp ? new Date(simTimestamp).toISOString() : undefined,
      });
      // Awaited so the busy state (and the button it disables) doesn't clear
      // until the refreshed confirmed-donor list has actually landed — closes
      // a window where a fast second click could act on stale progress data.
      await loadBlastDetails(activeBlastId);
    } catch (err) {
      setSimError(err instanceof Error ? err.message : "Failed to simulate reply");
    } finally {
      setSimBusy(false);
    }
  }

  async function handleForceExpire() {
    if (activeBlastId === null) return;
    setSimBusy(true);
    setSimError(null);
    try {
      await apiPost(`/donors/blasts/${activeBlastId}/dev-force-expire`, {});
      await loadBlastDetails(activeBlastId);
    } catch (err) {
      setSimError(err instanceof Error ? err.message : "Failed to force expire");
    } finally {
      setSimBusy(false);
    }
  }

  const messagedDonors = blastMessagesData
    ? Array.from(new Map(blastMessagesData.messages.map((m) => [m.donor_id, m])).values())
    : [];

  const confirmedCount = confirmedData?.confirmed_count ?? 0;
  const target = blastSummary?.target_count ?? targetCount;
  const isCompleted = blastSummary?.status === "completed";

  return (
    <div className="max-w-screen-2xl mx-auto px-6 py-6 space-y-6">
      <div className="grid lg:grid-cols-5 gap-5">
        {/* Compose panel */}
        <div className="lg:col-span-2 space-y-4">
          {/* Donor roster upload */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center">
                <Upload size={15} className="text-primary" />
              </div>
              <div>
                <h3 className="font-semibold text-foreground">Donor Roster</h3>
                <p className="text-[13px] text-muted-foreground">
                  {donorCount === null ? "Loading…" : `${donorCount} donors on file`}
                </p>
              </div>
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
              className="w-full h-9 bg-card border border-border rounded-lg text-[13px] font-semibold text-foreground hover:bg-secondary transition-colors flex items-center justify-center gap-1.5 disabled:opacity-60"
            >
              <Upload size={14} /> {uploading ? "Uploading…" : "Upload Donor CSV"}
            </button>
            <p className="text-[12px] text-muted-foreground mt-1.5">
              Columns: name, blood_type, phone. Re-uploading updates existing donors by phone number.
            </p>
            {uploadError && (
              <div className="mt-2 text-[12px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-2.5 py-2">
                {uploadError}
              </div>
            )}
            {uploadResult && (
              <div className="mt-2 text-[12px] bg-secondary rounded-lg px-2.5 py-2 space-y-1">
                <div className="font-semibold text-foreground">{uploadResult.rows_processed} donors processed</div>
                {uploadResult.errors.map((e, i) => (
                  <div key={i} className="text-status-critical-text">
                    Row {e.row}: {e.reason}
                  </div>
                ))}
              </div>
            )}
          </div>

          <UploadHistoryPanel uploadType="donors" refreshKey={uploadHistoryRefreshKey} onUndone={refreshDonorCount} />

          {/* Blast composer */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-center gap-2 mb-5">
              <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center">
                <Phone size={15} className="text-primary" />
              </div>
              <div>
                <h3 className="font-semibold text-foreground">SMS Donor Blast</h3>
                <p className="text-[13px] text-muted-foreground">Simulated — no real provider connected</p>
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <label className="text-[13px] font-semibold text-foreground block mb-2">Blood Type</label>
                <div className="grid grid-cols-4 gap-1.5">
                  {ALL_BLOOD_TYPES.map((bt) => (
                    <button
                      key={bt}
                      onClick={() => setSelectedType(bt)}
                      className={`py-2 text-[14px] font-display font-bold rounded-lg border transition-all duration-200 ease-out ${
                        selectedType === bt
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
                  <label className="text-[13px] font-semibold text-foreground block mb-1.5">
                    Donors needed
                  </label>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setTargetCount(Math.max(1, targetCount - 1))}
                      className="w-8 h-8 border border-border rounded-lg text-foreground font-bold hover:bg-secondary flex items-center justify-center"
                    >
                      −
                    </button>
                    <span className="text-lg font-bold text-foreground w-8 text-center">{targetCount}</span>
                    <button
                      onClick={() => setTargetCount(targetCount + 1)}
                      className="w-8 h-8 border border-border rounded-lg text-foreground font-bold hover:bg-secondary flex items-center justify-center"
                    >
                      +
                    </button>
                  </div>
                </div>
                <div>
                  <label className="text-[13px] font-semibold text-foreground block mb-1.5">
                    Time limit (hrs)
                  </label>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setTimeLimitHours(Math.max(1, timeLimitHours - 1))}
                      className="w-8 h-8 border border-border rounded-lg text-foreground font-bold hover:bg-secondary flex items-center justify-center"
                    >
                      −
                    </button>
                    <span className="text-lg font-bold text-foreground w-8 text-center">{timeLimitHours}</span>
                    <button
                      onClick={() => setTimeLimitHours(timeLimitHours + 1)}
                      className="w-8 h-8 border border-border rounded-lg text-foreground font-bold hover:bg-secondary flex items-center justify-center"
                    >
                      +
                    </button>
                  </div>
                </div>
              </div>

              {createError && (
                <div className="text-[12px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-2.5 py-2">
                  {createError}
                </div>
              )}

              <button
                onClick={handleCreateBlast}
                disabled={creatingBlast}
                className="w-full h-10 bg-primary text-white text-[14px] font-semibold rounded-lg hover:bg-primary-hover transition-colors flex items-center justify-center gap-2 disabled:opacity-60"
              >
                <Send size={15} />
                {creatingBlast ? "Sending…" : "Send Blast"}
              </button>
            </div>
          </div>

          {/* Dev-only reply/expiry simulation tools */}
          {isDevMode && activeBlastId !== null && (
            <div className="bg-status-watch-tint border border-status-watch-border rounded-xl p-5 space-y-3">
              <div className="flex items-center gap-1.5 text-[12px] font-bold text-status-watch-text uppercase tracking-wide">
                <AlertTriangle size={14} /> Dev Tools — Simulate Donor Activity
              </div>
              <p className="text-[12px] text-status-watch-text leading-snug">
                Stands in for a real donor's SMS reply, since no provider is connected. Only works because this
                server has ALLOW_DEV_TEST_TOOLS enabled.
              </p>

              <div>
                <label className="text-[12px] font-semibold text-status-watch-text block mb-1">Donor</label>
                <select
                  value={simDonorId}
                  onChange={(e) => setSimDonorId(e.target.value ? Number(e.target.value) : "")}
                  className="w-full h-8 px-2 rounded-md border border-status-watch-border bg-card text-[13px] text-foreground"
                >
                  <option value="">Select a messaged donor…</option>
                  {messagedDonors.map((d) => (
                    <option key={d.donor_id} value={d.donor_id}>
                      {d.donor_name} ({d.phone})
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[12px] font-semibold text-status-watch-text block mb-1">Reply</label>
                  <div className="flex gap-1.5">
                    {(["yes", "no"] as const).map((r) => (
                      <button
                        key={r}
                        onClick={() => setSimReply(r)}
                        className={`flex-1 h-8 text-[13px] font-bold rounded-md border transition-all duration-200 ease-out ${
                          simReply === r
                            ? "bg-primary text-white border-primary scale-105"
                            : "bg-card text-foreground border-status-watch-border"
                        }`}
                      >
                        {r.toUpperCase()}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-[12px] font-semibold text-status-watch-text block mb-1">
                    Reply time (optional)
                  </label>
                  <input
                    type="datetime-local"
                    value={simTimestamp}
                    onChange={(e) => setSimTimestamp(e.target.value)}
                    className="w-full h-8 px-2 rounded-md border border-status-watch-border bg-card text-[12px] text-foreground"
                  />
                </div>
              </div>

              {simError && (
                <div className="text-[12px] text-status-critical-text bg-status-critical-tint border border-status-critical-border rounded-lg px-2.5 py-2">
                  {simError}
                </div>
              )}

              <button
                onClick={handleSimulateReply}
                disabled={simBusy || simDonorId === ""}
                className="w-full h-8 bg-status-watch text-white text-[13px] font-semibold rounded-lg hover:bg-status-watch-text transition-colors disabled:opacity-60"
              >
                {simBusy ? "Working…" : "Simulate Reply"}
              </button>
              <button
                onClick={handleForceExpire}
                disabled={simBusy}
                className="w-full h-8 bg-card border border-status-watch-border text-status-watch-text text-[13px] font-semibold rounded-lg hover:bg-status-watch-tint transition-colors disabled:opacity-60"
              >
                Force Deadline Into the Past
              </button>
            </div>
          )}
        </div>

        {/* Results panel */}
        <div className="lg:col-span-3 space-y-4">
          {!blastSummary && !detailLoading && (
            <div className="bg-card border border-border rounded-xl p-10 flex flex-col items-center text-center">
              <div className="w-14 h-14 rounded-full bg-primary-tint flex items-center justify-center mb-4">
                <Send size={22} className="text-primary" />
              </div>
              <h3 className="font-semibold text-foreground mb-1">No blast sent yet</h3>
              <p className="text-[14px] text-muted-foreground max-w-xs leading-relaxed">
                Pick a blood type and donor count on the left, then send to start tracking confirmations here.
              </p>
            </div>
          )}

          {blastSummary && (
            <div key={blastSummary.id} className="animate-success-pop space-y-4">
              {/* SIMULATED banner — deliberately the most visually prominent
                  element on this screen, using the exact label text the
                  backend returns, so there's one source of truth for the wording. */}
              {blastMessagesData && (
                <div className="bg-info-tint border-2 border-info-border rounded-xl px-4 py-3 flex items-center gap-2.5">
                  <MessageSquare size={18} className="text-info-text shrink-0" />
                  <div>
                    <div className="text-[14px] font-bold text-info-text">{blastMessagesData.label}</div>
                    <div className="text-[12px] text-info-text">
                      No real SMS provider is connected — this is a logged simulation only.
                    </div>
                  </div>
                </div>
              )}

              <div className="bg-card border border-border rounded-xl">
                <div className="px-5 py-4 border-b border-border flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-foreground">
                      {blastSummary.blood_type} Donor Drive
                      {isDevMode && <span className="text-muted-foreground font-normal"> · blast #{blastSummary.id}</span>}
                    </h3>
                    <p className="text-[13px] text-muted-foreground mt-0.5">
                      Target {target} donors within {blastSummary.time_limit_hours}h
                    </p>
                  </div>
                  {isCompleted ? (
                    <span className="flex items-center gap-1 text-[13px] font-semibold bg-status-safe-tint text-status-safe-text px-3 py-1 rounded-full border border-status-safe-border">
                      <CheckCircle size={13} /> Drive Complete
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-[13px] font-semibold bg-status-watch-tint text-status-watch-text px-3 py-1 rounded-full border border-status-watch-border">
                      <Clock size={13} /> Active
                    </span>
                  )}
                </div>

                <div className="px-5 py-3 border-b border-border">
                  <div className="flex justify-between text-[12px] font-semibold mb-1.5">
                    <span className="text-muted-foreground">Confirmation progress</span>
                    <span className="text-status-safe-text">{confirmedCount} of {target} needed</span>
                  </div>
                  <div className="h-2 bg-secondary rounded-full overflow-hidden">
                    <div
                      className="h-full bg-status-safe rounded-full transition-all"
                      style={{ width: `${Math.min(100, (confirmedCount / target) * 100)}%` }}
                    />
                  </div>
                </div>

                {detailError && (
                  <div className="px-5 py-3 text-[13px] text-status-critical-text">Failed to load: {detailError}</div>
                )}

                {confirmedData && confirmedData.confirmed_donors.length === 0 && (
                  <div className="px-5 py-8 text-center text-[13px] text-muted-foreground">
                    No confirmed donors yet.
                  </div>
                )}

                {confirmedData && confirmedData.confirmed_donors.length > 0 && (
                  <div className="divide-y divide-border">
                    {confirmedData.confirmed_donors.map((donor, i) => (
                      <div key={`${donor.phone}-${i}`} className="px-5 py-3 flex items-center gap-4">
                        <span className="text-[11px] font-bold bg-primary/10 text-primary px-1.5 py-0.5 rounded shrink-0">
                          #{i + 1}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="font-semibold text-[14px] text-foreground">{donor.name}</div>
                          <div className="text-[12px] text-muted-foreground font-mono mt-0.5">{donor.phone}</div>
                        </div>
                        <div className="text-[12px] text-muted-foreground font-mono shrink-0">
                          {new Date(donor.replied_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </div>
                        <span className="flex items-center gap-1 text-[12px] font-semibold text-status-safe-text bg-status-safe-tint px-2.5 py-1 rounded-full border border-status-safe-border shrink-0">
                          <CheckCircle size={12} /> Confirmed
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Simulated message log */}
              {blastMessagesData && (
                <div className="bg-card border border-border rounded-xl">
                  <div className="px-5 py-4 border-b border-border">
                    <h3 className="font-semibold text-foreground">Simulated Message Log</h3>
                    <p className="text-[13px] text-muted-foreground mt-0.5">
                      {blastMessagesData.messages.length} log entries — every donor messaged, including drive-complete follow-ups
                    </p>
                  </div>
                  <div className="divide-y divide-border max-h-80 overflow-y-auto">
                    {blastMessagesData.messages.map((m, i) => (
                      <div key={i} className="px-5 py-3">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-semibold text-[13px] text-foreground">{m.donor_name}</span>
                          <span className="text-[12px] text-muted-foreground font-mono">{m.phone}</span>
                        </div>
                        <div className="text-[12px] text-muted-foreground leading-relaxed">{m.message_text}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

