-- Run this ONCE in Supabase: Dashboard > SQL Editor > New query > paste > Run.
-- It creates two tables. Row Level Security (RLS) means every signed-in person can only
-- see and change THEIR OWN rows, even though the browser talks to the database directly.

-- 1) One profile per person: display name + the seed that decides how their bot looks.
create table if not exists public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  bot_seed    text,
  created_at  timestamptz not null default now()
);

-- 2) The assistant chat history, so the conversation is still there after a refresh.
create table if not exists public.chat_messages (
  id         bigint generated always as identity primary key,
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  role       text not null check (role in ('user', 'bot')),
  content    text not null check (char_length(content) <= 4000),
  created_at timestamptz not null default now()
);
create index if not exists chat_messages_user_time on public.chat_messages (user_id, created_at desc);

alter table public.profiles      enable row level security;
alter table public.chat_messages enable row level security;

-- "auth.uid()" is the id of whoever is signed in. The wrapping select makes Postgres evaluate it once.
drop policy if exists "own profile read"   on public.profiles;
drop policy if exists "own profile insert" on public.profiles;
drop policy if exists "own profile update" on public.profiles;
create policy "own profile read"   on public.profiles for select using ((select auth.uid()) = id);
create policy "own profile insert" on public.profiles for insert with check ((select auth.uid()) = id);
create policy "own profile update" on public.profiles for update using ((select auth.uid()) = id);

drop policy if exists "own messages read"   on public.chat_messages;
drop policy if exists "own messages insert" on public.chat_messages;
drop policy if exists "own messages delete" on public.chat_messages;
create policy "own messages read"   on public.chat_messages for select using ((select auth.uid()) = user_id);
create policy "own messages insert" on public.chat_messages for insert with check ((select auth.uid()) = user_id);
create policy "own messages delete" on public.chat_messages for delete using ((select auth.uid()) = user_id);
