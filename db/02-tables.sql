-- Tabelas mínimas necessárias pela função (campos essenciais)

create table if not exists faccoes (
  faccao_id   bigint primary key,
  created_at  timestamp default now(),
  nome        text not null
);

create table if not exists funcoes (
  funcao_id   bigint primary key,
  nome        text not null,
  faccao_id   bigint references faccoes(faccao_id)
);

create table if not exists membros (
  membro_id     bigint primary key,
  created_at    timestamp default now(),
  nome_completo text,
  alcunha       text[],          -- text[] como no Supabase
  faccao_id     bigint references faccoes(faccao_id),
  funcao_id     bigint references funcoes(funcao_id)
);

-- Permissões mínimas (leitura + executar função depois)
grant select on all tables in schema public to anon, service_role;
alter default privileges in schema public grant select on tables to anon, service_role;
