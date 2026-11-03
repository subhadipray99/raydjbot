-- =============================================================================
-- ZevRay Highrise DJ Bot SaaS Database Schema
-- Supabase Project: ahvvanhxbqtcbtuwfxtm
-- =============================================================================

-- 1. Room State Persistence (Position, Floors, Outfit, Welcome, Tips)
CREATE TABLE IF NOT EXISTS public.room_state (
    room_id TEXT PRIMARY KEY,
    saved_position JSONB DEFAULT '{}'::jsonb,
    floors JSONB DEFAULT '{}'::jsonb,
    active_outfit JSONB DEFAULT '[]'::jsonb,
    custom_welcome TEXT DEFAULT '',
    top_tipper JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable Row Level Security (RLS)
ALTER TABLE public.room_state ENABLE ROW LEVEL SECURITY;

-- Allow public read of room state
CREATE POLICY "Public read room_state"
    ON public.room_state FOR SELECT
    USING (true);

-- Allow full access to service_role
CREATE POLICY "Service role full access room_state"
    ON public.room_state FOR ALL
    USING (true)
    WITH CHECK (true);


-- 2. Deployed Bots Registry
CREATE TABLE IF NOT EXISTS public.bots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    room_id TEXT NOT NULL,
    room_owner TEXT NOT NULL,
    room_title TEXT DEFAULT 'My Lounge',
    render_service_id TEXT,
    render_service_name TEXT,
    render_service_url TEXT,
    radio_stream_url TEXT,
    health_url TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.bots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users read own bots"
    ON public.bots FOR SELECT
    USING (auth.uid() = user_id OR auth.role() = 'service_role');

CREATE POLICY "Service role full access bots"
    ON public.bots FOR ALL
    USING (true)
    WITH CHECK (true);


-- 3. Customer Profiles & Subscription Status
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    highrise_username TEXT,
    full_name TEXT,
    avatar_url TEXT,
    saved_config JSONB DEFAULT '{}'::jsonb,
    subscription_status TEXT DEFAULT 'free',
    subscription_tier TEXT DEFAULT 'free',
    subscription_expires_at TIMESTAMPTZ DEFAULT NULL,
    subscription_currency TEXT DEFAULT 'INR',
    subscription_data JSONB DEFAULT '{}'::jsonb,
    razorpay_customer_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ensure columns exist if table was already created earlier
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS full_name TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS avatar_url TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS saved_config JSONB DEFAULT '{}'::jsonb;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS subscription_tier TEXT DEFAULT 'free';
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMPTZ DEFAULT NULL;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS subscription_currency TEXT DEFAULT 'INR';
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS subscription_data JSONB DEFAULT '{}'::jsonb;

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users read own profile"
    ON public.profiles FOR SELECT
    USING (auth.uid() = id OR auth.role() = 'service_role');

CREATE POLICY "Service role full access profiles"
    ON public.profiles FOR ALL
    USING (true)
    WITH CHECK (true);

-- 4. Payments Audit Log
CREATE TABLE IF NOT EXISTS public.payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    gateway TEXT DEFAULT 'razorpay',
    order_id TEXT,
    payment_id TEXT,
    status TEXT DEFAULT 'completed',
    starts_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users read own payments"
    ON public.payments FOR SELECT
    USING (auth.uid() = user_id OR auth.role() = 'service_role');

CREATE POLICY "Service role full access payments"
    ON public.payments FOR ALL
    USING (true)
    WITH CHECK (true);

-- Auto-create/update profile trigger on Supabase Auth signup & Google OAuth logins
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.profiles (id, email, highrise_username, full_name, avatar_url)
    VALUES (
        new.id,
        new.email,
        COALESCE(new.raw_user_meta_data->>'highrise_username', ''),
        COALESCE(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', ''),
        COALESCE(new.raw_user_meta_data->>'avatar_url', new.raw_user_meta_data->>'picture', '')
    )
    ON CONFLICT (id) DO UPDATE SET
        email = EXCLUDED.email,
        full_name = COALESCE(NULLIF(EXCLUDED.full_name, ''), public.profiles.full_name),
        avatar_url = COALESCE(NULLIF(EXCLUDED.avatar_url, ''), public.profiles.avatar_url);
    RETURN new;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
