-- Design system v4: which style and tone each post was rendered with.
-- Additive, nullable → old rows and old code keep working.
alter table campaigns add column if not exists design_style text
  check (design_style is null or design_style in ('offer','editorial','fact','price_hero','multi','minimal'));
alter table campaigns add column if not exists design_tone text
  check (design_tone is null or design_tone in ('light','dark'));
