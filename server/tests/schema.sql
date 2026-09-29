--
-- PostgreSQL database dump
--

\restrict 8hDJh7iq2rNf1M8kAxwKMJCvXGhAxePp09fUdmuLIbXxREYIyNkRedyKAcuBpFU

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.11

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: blast_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blast_messages (
    id bigint NOT NULL,
    blast_id bigint NOT NULL,
    donor_id bigint NOT NULL,
    message_text text NOT NULL,
    simulated_sent_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: blast_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.blast_messages ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.blast_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: blast_replies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blast_replies (
    id bigint NOT NULL,
    blast_id bigint NOT NULL,
    donor_id bigint NOT NULL,
    reply text NOT NULL,
    replied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: blast_replies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.blast_replies ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.blast_replies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: blasts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blasts (
    id bigint NOT NULL,
    facility_id bigint NOT NULL,
    blood_type text NOT NULL,
    target_count integer NOT NULL,
    time_limit_hours integer NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deadline_at timestamp with time zone NOT NULL
);


--
-- Name: blasts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.blasts ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.blasts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: blood_type_thresholds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blood_type_thresholds (
    blood_type text NOT NULL,
    minimum_units integer NOT NULL,
    maximum_units integer NOT NULL,
    facility_id bigint NOT NULL
);


--
-- Name: blood_units; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blood_units (
    id bigint NOT NULL,
    din text NOT NULL,
    blood_type text NOT NULL,
    component text NOT NULL,
    location text NOT NULL,
    volume_ml integer NOT NULL,
    collected_date date NOT NULL,
    expires_date date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    facility_id bigint NOT NULL,
    reserved_for_request_id bigint,
    last_notified_expiry_status text,
    upload_history_id bigint,
    archived_at timestamp with time zone
);


--
-- Name: blood_units_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.blood_units ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.blood_units_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: donors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.donors (
    id bigint NOT NULL,
    name text NOT NULL,
    blood_type text NOT NULL,
    phone text NOT NULL,
    facility_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    upload_history_id bigint
);


--
-- Name: donors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.donors ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.donors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: facilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facilities (
    id bigint NOT NULL,
    name text NOT NULL,
    facility_type text NOT NULL,
    address text,
    latitude double precision,
    longitude double precision,
    department text,
    doh_license_number text,
    profile_completed boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: facilities_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.facilities ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.facilities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: facility_forecast_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facility_forecast_cache (
    id bigint NOT NULL,
    facility_id bigint NOT NULL,
    blood_type text NOT NULL,
    forecast_date date NOT NULL,
    forecast_units numeric NOT NULL,
    lower_units numeric NOT NULL,
    upper_units numeric NOT NULL,
    trained_through_date date NOT NULL,
    model_order text NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE facility_forecast_cache; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.facility_forecast_cache IS 'Real per-facility SARIMAX forecast cache, fit on that facility''s own inventory_snapshots history. Never blended with synthetic_forecast_cache.';


--
-- Name: facility_forecast_cache_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.facility_forecast_cache ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.facility_forecast_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: facility_registration_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facility_registration_requests (
    id bigint NOT NULL,
    facility_name text NOT NULL,
    facility_type text NOT NULL,
    address text NOT NULL,
    latitude double precision,
    longitude double precision,
    doh_license_number text NOT NULL,
    contact_person text NOT NULL,
    email text NOT NULL,
    phone text NOT NULL,
    password_hash text NOT NULL,
    status text DEFAULT 'submitted'::text NOT NULL,
    rejection_reason text,
    reviewed_by bigint,
    reviewed_at timestamp with time zone,
    archived_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT facility_registration_requests_status_valid CHECK ((status = ANY (ARRAY['submitted'::text, 'email_verified'::text, 'approved'::text, 'rejected'::text]))),
    CONSTRAINT facility_registration_requests_type_valid CHECK ((facility_type = ANY (ARRAY['hospital'::text, 'bloodbank'::text])))
);


--
-- Name: facility_registration_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.facility_registration_requests ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.facility_registration_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: facility_sarimax_order; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facility_sarimax_order (
    facility_id bigint NOT NULL,
    blood_type text NOT NULL,
    p smallint NOT NULL,
    d smallint NOT NULL,
    q smallint NOT NULL,
    seasonal_p smallint NOT NULL,
    seasonal_d smallint NOT NULL,
    seasonal_q smallint NOT NULL,
    seasonal_s smallint NOT NULL,
    aic double precision NOT NULL,
    lb_p_14_in_sample double precision NOT NULL,
    lb_p_21_in_sample double precision NOT NULL,
    lb_p_28_in_sample double precision NOT NULL,
    criterion_satisfied boolean NOT NULL,
    n_candidates_converged smallint NOT NULL,
    lb_p_14_holdout_onestep double precision,
    lb_p_21_holdout_onestep double precision,
    lb_p_28_holdout_onestep double precision,
    holdout_mape double precision,
    holdout_rmse double precision,
    holdout_baseline_mape double precision,
    holdout_baseline_rmse double precision,
    trained_through_date date NOT NULL,
    evaluated_through_date date NOT NULL,
    selected_at timestamp with time zone DEFAULT now() NOT NULL,
    start_params jsonb,
    selection_aic_per_obs double precision
);


--
-- Name: TABLE facility_sarimax_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.facility_sarimax_order IS 'Per-facility, per-blood-type SARIMAX order chosen offline by grid search on TRAINING data only (main.py:_run_order_selection_for_facility / server/select_orders.py), with diagnostics reported separately on a 30-day hold-out the selection never saw. Absent = the live fitter falls back to the fixed FACILITY_SARIMAX_ORDER.';


--
-- Name: forecast_alert_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.forecast_alert_state (
    facility_id bigint NOT NULL,
    blood_type text NOT NULL,
    alerting boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: inventory_snapshot_write_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventory_snapshot_write_log (
    id bigint NOT NULL,
    facility_id bigint NOT NULL,
    snapshot_date date NOT NULL,
    blood_type text NOT NULL,
    units integer NOT NULL,
    upload_history_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: inventory_snapshot_write_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.inventory_snapshot_write_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.inventory_snapshot_write_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: inventory_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.inventory_snapshots (
    id bigint NOT NULL,
    snapshot_date date NOT NULL,
    blood_type text NOT NULL,
    units integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    facility_id bigint NOT NULL,
    upload_history_id bigint
);


--
-- Name: inventory_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.inventory_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.inventory_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id bigint NOT NULL,
    facility_id bigint NOT NULL,
    type text NOT NULL,
    message text NOT NULL,
    link text,
    read_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.notifications ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.notifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: otp_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.otp_codes (
    id bigint NOT NULL,
    purpose text NOT NULL,
    registration_request_id bigint,
    user_id bigint,
    email text NOT NULL,
    code_hash text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT otp_codes_binding_matches_purpose CHECK ((((purpose = 'registration_email_verify'::text) AND (registration_request_id IS NOT NULL) AND (user_id IS NULL)) OR ((purpose = 'password_reset'::text) AND (user_id IS NOT NULL) AND (registration_request_id IS NULL)))),
    CONSTRAINT otp_codes_purpose_valid CHECK ((purpose = ANY (ARRAY['registration_email_verify'::text, 'password_reset'::text])))
);


--
-- Name: otp_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.otp_codes ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.otp_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: password_reset_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_requests (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: password_reset_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.password_reset_requests ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.password_reset_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: request_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_messages (
    id bigint NOT NULL,
    request_id bigint NOT NULL,
    sender_facility_id bigint NOT NULL,
    message text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: request_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.request_messages ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.request_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: request_unit_failures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.request_unit_failures (
    id bigint NOT NULL,
    request_id bigint NOT NULL,
    blood_unit_id bigint NOT NULL,
    din text NOT NULL,
    expires_date date NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT request_unit_failures_reason_check CHECK ((reason = 'expired_before_receipt'::text))
);


--
-- Name: request_unit_failures_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.request_unit_failures ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.request_unit_failures_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.requests (
    id bigint NOT NULL,
    requesting_facility_id bigint NOT NULL,
    supplying_facility_id bigint NOT NULL,
    blood_type text NOT NULL,
    quantity integer NOT NULL,
    emergency_type text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    supplier_confirmed_at timestamp with time zone,
    requester_confirmed_at timestamp with time zone
);


--
-- Name: requests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.requests ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: synthetic_forecast_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.synthetic_forecast_cache (
    id bigint NOT NULL,
    blood_type text NOT NULL,
    forecast_date date NOT NULL,
    forecast_units numeric NOT NULL,
    model_order text NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE synthetic_forecast_cache; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.synthetic_forecast_cache IS 'SYNTHETIC MODEL OUTPUT â€” generated by fit_and_cache_synthetic_forecast.py, not real. Stand-in for /forecast while real facility history is insufficient. See SYNTHETIC_ARIMAX_VALIDATION.md.';


--
-- Name: synthetic_forecast_cache_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.synthetic_forecast_cache ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.synthetic_forecast_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: synthetic_inventory_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.synthetic_inventory_snapshots (
    id bigint NOT NULL,
    snapshot_date date NOT NULL,
    blood_type text NOT NULL,
    units integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE synthetic_inventory_snapshots; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.synthetic_inventory_snapshots IS 'SYNTHETIC TEST DATA â€” generated, not real. Used only for ARIMAX model development pending real historical data from a facility interview. Never join or blend with inventory_snapshots.';


--
-- Name: synthetic_inventory_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.synthetic_inventory_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.synthetic_inventory_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: upload_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.upload_history (
    id bigint NOT NULL,
    facility_id bigint NOT NULL,
    upload_type text NOT NULL,
    uploaded_by bigint,
    filename text,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    rows_processed integer NOT NULL,
    rows_failed integer NOT NULL,
    error_details jsonb DEFAULT '[]'::jsonb NOT NULL,
    raw_content text,
    undone_at timestamp with time zone,
    CONSTRAINT upload_history_upload_type_check CHECK ((upload_type = ANY (ARRAY['inventory'::text, 'donors'::text, 'historical_stock'::text])))
);


--
-- Name: upload_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.upload_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.upload_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    facility_id bigint,
    role text DEFAULT 'staff'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.users ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: blast_messages blast_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_messages
    ADD CONSTRAINT blast_messages_pkey PRIMARY KEY (id);


--
-- Name: blast_replies blast_replies_blast_id_donor_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_replies
    ADD CONSTRAINT blast_replies_blast_id_donor_id_key UNIQUE (blast_id, donor_id);


--
-- Name: blast_replies blast_replies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_replies
    ADD CONSTRAINT blast_replies_pkey PRIMARY KEY (id);


--
-- Name: blasts blasts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blasts
    ADD CONSTRAINT blasts_pkey PRIMARY KEY (id);


--
-- Name: blood_type_thresholds blood_type_thresholds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_type_thresholds
    ADD CONSTRAINT blood_type_thresholds_pkey PRIMARY KEY (facility_id, blood_type);


--
-- Name: blood_units blood_units_din_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_units
    ADD CONSTRAINT blood_units_din_key UNIQUE (din);


--
-- Name: blood_units blood_units_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_units
    ADD CONSTRAINT blood_units_pkey PRIMARY KEY (id);


--
-- Name: donors donors_facility_id_phone_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.donors
    ADD CONSTRAINT donors_facility_id_phone_key UNIQUE (facility_id, phone);


--
-- Name: donors donors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.donors
    ADD CONSTRAINT donors_pkey PRIMARY KEY (id);


--
-- Name: facilities facilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facilities
    ADD CONSTRAINT facilities_pkey PRIMARY KEY (id);


--
-- Name: facility_forecast_cache facility_forecast_cache_facility_id_blood_type_forecast_dat_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_forecast_cache
    ADD CONSTRAINT facility_forecast_cache_facility_id_blood_type_forecast_dat_key UNIQUE (facility_id, blood_type, forecast_date);


--
-- Name: facility_forecast_cache facility_forecast_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_forecast_cache
    ADD CONSTRAINT facility_forecast_cache_pkey PRIMARY KEY (id);


--
-- Name: facility_registration_requests facility_registration_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_registration_requests
    ADD CONSTRAINT facility_registration_requests_pkey PRIMARY KEY (id);


--
-- Name: facility_sarimax_order facility_sarimax_order_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_sarimax_order
    ADD CONSTRAINT facility_sarimax_order_pkey PRIMARY KEY (facility_id, blood_type);


--
-- Name: forecast_alert_state forecast_alert_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forecast_alert_state
    ADD CONSTRAINT forecast_alert_state_pkey PRIMARY KEY (facility_id, blood_type);


--
-- Name: inventory_snapshot_write_log inventory_snapshot_write_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshot_write_log
    ADD CONSTRAINT inventory_snapshot_write_log_pkey PRIMARY KEY (id);


--
-- Name: inventory_snapshots inventory_snapshots_date_type_facility_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshots
    ADD CONSTRAINT inventory_snapshots_date_type_facility_key UNIQUE (snapshot_date, blood_type, facility_id);


--
-- Name: inventory_snapshots inventory_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshots
    ADD CONSTRAINT inventory_snapshots_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: otp_codes otp_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.otp_codes
    ADD CONSTRAINT otp_codes_pkey PRIMARY KEY (id);


--
-- Name: password_reset_requests password_reset_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_requests
    ADD CONSTRAINT password_reset_requests_pkey PRIMARY KEY (id);


--
-- Name: password_reset_requests password_reset_requests_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_requests
    ADD CONSTRAINT password_reset_requests_token_hash_key UNIQUE (token_hash);


--
-- Name: request_messages request_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_messages
    ADD CONSTRAINT request_messages_pkey PRIMARY KEY (id);


--
-- Name: request_unit_failures request_unit_failures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_unit_failures
    ADD CONSTRAINT request_unit_failures_pkey PRIMARY KEY (id);


--
-- Name: request_unit_failures request_unit_failures_request_id_blood_unit_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_unit_failures
    ADD CONSTRAINT request_unit_failures_request_id_blood_unit_id_key UNIQUE (request_id, blood_unit_id);


--
-- Name: requests requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (id);


--
-- Name: synthetic_forecast_cache synthetic_forecast_cache_blood_type_forecast_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.synthetic_forecast_cache
    ADD CONSTRAINT synthetic_forecast_cache_blood_type_forecast_date_key UNIQUE (blood_type, forecast_date);


--
-- Name: synthetic_forecast_cache synthetic_forecast_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.synthetic_forecast_cache
    ADD CONSTRAINT synthetic_forecast_cache_pkey PRIMARY KEY (id);


--
-- Name: synthetic_inventory_snapshots synthetic_inventory_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.synthetic_inventory_snapshots
    ADD CONSTRAINT synthetic_inventory_snapshots_pkey PRIMARY KEY (id);


--
-- Name: synthetic_inventory_snapshots synthetic_inventory_snapshots_snapshot_date_blood_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.synthetic_inventory_snapshots
    ADD CONSTRAINT synthetic_inventory_snapshots_snapshot_date_blood_type_key UNIQUE (snapshot_date, blood_type);


--
-- Name: upload_history upload_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upload_history
    ADD CONSTRAINT upload_history_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_blood_units_upload_history; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blood_units_upload_history ON public.blood_units USING btree (upload_history_id);


--
-- Name: idx_donors_upload_history; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_donors_upload_history ON public.donors USING btree (upload_history_id);


--
-- Name: idx_facility_registration_requests_email_live; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_facility_registration_requests_email_live ON public.facility_registration_requests USING btree (email) WHERE (archived_at IS NULL);


--
-- Name: idx_facility_registration_requests_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facility_registration_requests_queue ON public.facility_registration_requests USING btree (status) WHERE (archived_at IS NULL);


--
-- Name: idx_inventory_snapshots_upload_history; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_inventory_snapshots_upload_history ON public.inventory_snapshots USING btree (upload_history_id);


--
-- Name: idx_notifications_facility_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notifications_facility_created ON public.notifications USING btree (facility_id, created_at DESC);


--
-- Name: idx_notifications_facility_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notifications_facility_unread ON public.notifications USING btree (facility_id) WHERE (read_at IS NULL);


--
-- Name: idx_otp_codes_registration_request; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_otp_codes_registration_request ON public.otp_codes USING btree (registration_request_id) WHERE (registration_request_id IS NOT NULL);


--
-- Name: idx_otp_codes_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_otp_codes_user ON public.otp_codes USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: idx_password_reset_requests_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_requests_token_hash ON public.password_reset_requests USING btree (token_hash);


--
-- Name: idx_request_messages_request_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_messages_request_id ON public.request_messages USING btree (request_id);


--
-- Name: idx_request_unit_failures_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_request_unit_failures_unit ON public.request_unit_failures USING btree (blood_unit_id);


--
-- Name: idx_snapshot_write_log_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshot_write_log_key ON public.inventory_snapshot_write_log USING btree (facility_id, snapshot_date, blood_type);


--
-- Name: idx_snapshot_write_log_upload; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshot_write_log_upload ON public.inventory_snapshot_write_log USING btree (upload_history_id);


--
-- Name: idx_upload_history_facility_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_upload_history_facility_type ON public.upload_history USING btree (facility_id, upload_type, uploaded_at DESC);


--
-- Name: blast_messages blast_messages_blast_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_messages
    ADD CONSTRAINT blast_messages_blast_id_fkey FOREIGN KEY (blast_id) REFERENCES public.blasts(id);


--
-- Name: blast_messages blast_messages_donor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_messages
    ADD CONSTRAINT blast_messages_donor_id_fkey FOREIGN KEY (donor_id) REFERENCES public.donors(id);


--
-- Name: blast_replies blast_replies_blast_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_replies
    ADD CONSTRAINT blast_replies_blast_id_fkey FOREIGN KEY (blast_id) REFERENCES public.blasts(id);


--
-- Name: blast_replies blast_replies_donor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blast_replies
    ADD CONSTRAINT blast_replies_donor_id_fkey FOREIGN KEY (donor_id) REFERENCES public.donors(id);


--
-- Name: blasts blasts_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blasts
    ADD CONSTRAINT blasts_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: blood_type_thresholds blood_type_thresholds_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_type_thresholds
    ADD CONSTRAINT blood_type_thresholds_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: blood_units blood_units_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_units
    ADD CONSTRAINT blood_units_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: blood_units blood_units_reserved_for_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_units
    ADD CONSTRAINT blood_units_reserved_for_request_id_fkey FOREIGN KEY (reserved_for_request_id) REFERENCES public.requests(id);


--
-- Name: blood_units blood_units_upload_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blood_units
    ADD CONSTRAINT blood_units_upload_history_id_fkey FOREIGN KEY (upload_history_id) REFERENCES public.upload_history(id);


--
-- Name: donors donors_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.donors
    ADD CONSTRAINT donors_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: donors donors_upload_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.donors
    ADD CONSTRAINT donors_upload_history_id_fkey FOREIGN KEY (upload_history_id) REFERENCES public.upload_history(id);


--
-- Name: facility_forecast_cache facility_forecast_cache_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_forecast_cache
    ADD CONSTRAINT facility_forecast_cache_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: facility_registration_requests facility_registration_requests_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_registration_requests
    ADD CONSTRAINT facility_registration_requests_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id);


--
-- Name: facility_sarimax_order facility_sarimax_order_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_sarimax_order
    ADD CONSTRAINT facility_sarimax_order_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id) ON DELETE CASCADE;


--
-- Name: forecast_alert_state forecast_alert_state_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.forecast_alert_state
    ADD CONSTRAINT forecast_alert_state_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: inventory_snapshot_write_log inventory_snapshot_write_log_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshot_write_log
    ADD CONSTRAINT inventory_snapshot_write_log_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id) ON DELETE CASCADE;


--
-- Name: inventory_snapshot_write_log inventory_snapshot_write_log_upload_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshot_write_log
    ADD CONSTRAINT inventory_snapshot_write_log_upload_history_id_fkey FOREIGN KEY (upload_history_id) REFERENCES public.upload_history(id) ON DELETE CASCADE;


--
-- Name: inventory_snapshots inventory_snapshots_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshots
    ADD CONSTRAINT inventory_snapshots_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: inventory_snapshots inventory_snapshots_upload_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.inventory_snapshots
    ADD CONSTRAINT inventory_snapshots_upload_history_id_fkey FOREIGN KEY (upload_history_id) REFERENCES public.upload_history(id);


--
-- Name: notifications notifications_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: otp_codes otp_codes_registration_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.otp_codes
    ADD CONSTRAINT otp_codes_registration_request_id_fkey FOREIGN KEY (registration_request_id) REFERENCES public.facility_registration_requests(id);


--
-- Name: otp_codes otp_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.otp_codes
    ADD CONSTRAINT otp_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: password_reset_requests password_reset_requests_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_requests
    ADD CONSTRAINT password_reset_requests_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: request_messages request_messages_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_messages
    ADD CONSTRAINT request_messages_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.requests(id);


--
-- Name: request_messages request_messages_sender_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_messages
    ADD CONSTRAINT request_messages_sender_facility_id_fkey FOREIGN KEY (sender_facility_id) REFERENCES public.facilities(id);


--
-- Name: request_unit_failures request_unit_failures_blood_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_unit_failures
    ADD CONSTRAINT request_unit_failures_blood_unit_id_fkey FOREIGN KEY (blood_unit_id) REFERENCES public.blood_units(id);


--
-- Name: request_unit_failures request_unit_failures_request_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.request_unit_failures
    ADD CONSTRAINT request_unit_failures_request_id_fkey FOREIGN KEY (request_id) REFERENCES public.requests(id);


--
-- Name: requests requests_requesting_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_requesting_facility_id_fkey FOREIGN KEY (requesting_facility_id) REFERENCES public.facilities(id);


--
-- Name: requests requests_supplying_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_supplying_facility_id_fkey FOREIGN KEY (supplying_facility_id) REFERENCES public.facilities(id);


--
-- Name: upload_history upload_history_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upload_history
    ADD CONSTRAINT upload_history_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- Name: upload_history upload_history_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.upload_history
    ADD CONSTRAINT upload_history_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: users users_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id);


--
-- PostgreSQL database dump complete
--

\unrestrict 8hDJh7iq2rNf1M8kAxwKMJCvXGhAxePp09fUdmuLIbXxREYIyNkRedyKAcuBpFU

