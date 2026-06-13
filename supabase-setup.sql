-- ============================================================
-- 任天堂台灣遊戲監看 — Supabase 資料庫設定
-- 在 Supabase Dashboard > SQL Editor 執行此檔案
-- ============================================================

-- 1. 建立 user_data 資料表
CREATE TABLE IF NOT EXISTS public.user_data (
  user_id    UUID        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  favs       JSONB       NOT NULL DEFAULT '[]',
  owned      JSONB       NOT NULL DEFAULT '[]',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 啟用 Row Level Security（只有本人能讀寫自己的資料）
ALTER TABLE public.user_data ENABLE ROW LEVEL SECURITY;

-- 3. RLS Policy：登入用戶只能存取自己那筆資料
CREATE POLICY "Users manage own data"
  ON public.user_data
  FOR ALL
  USING      (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- ============================================================
-- 設定說明
-- ============================================================
-- 完成後，到 Supabase Dashboard > Authentication > URL Configuration：
--   Site URL          → 你的 GitHub Pages 網址
--                       例如 https://enix007-create.github.io/nintendo-tw/
--   Redirect URLs     → 加入相同網址（允許 magic link 導回）
--
-- 然後到 Project Settings > API，複製：
--   Project URL  → 填入 web/index.html 的 SUPABASE_URL
--   anon public  → 填入 web/index.html 的 SUPABASE_ANON
-- ============================================================
