-- ======================================================================
-- svc-kg / mondaha — Install script (IDEMPOTENTE)
-- ======================================================================
-- Propósito:
--   Instala no Postgres do mondaha a função `public.get_graph_membros`
--   + índices de suporte, permitindo que o microserviço svc-kg
--   (Knowledge Graph) leia o grafo de membros/facções/funções direto
--   do banco — SEM Supabase/PostgREST.
--
-- Migração Supabase -> Postgres mondaha:
--   - Removidos os `grant ... to anon/authenticated/service_role`
--     (roles específicos do Supabase).
--   - Removido `notify pgrst, 'reload schema'` (PostgREST schema cache).
--   - Função marcada como SECURITY INVOKER (Postgres direto, sem roles Supabase).
--   - Coluna `alcunha` no mondaha é `varchar[]` (array): label extrai o
--     1º valor não-vazio do array, com fallback para `nome_completo`.
--
-- Tabelas alvo (JÁ EXISTEM — este script NÃO cria/altera tabelas):
--   public.membros(membro_id int PK, nome_completo text, alcunha varchar[],
--                  faccao_id int, funcao_id int, ...)
--   public.faccoes(faccao_id int PK, nome text, ...)
--   public.funcoes(funcao_id int PK, nome text, faccao_id int, ...)
--
-- Idempotência: pode rodar múltiplas vezes sem erro
--   (CREATE OR REPLACE FUNCTION + CREATE INDEX IF NOT EXISTS).
-- ======================================================================

-- ----------------------------------------------------------------------
-- 1) Remove sobrecarga antiga em INTEGER (se existir), mantendo só BIGINT.
-- ----------------------------------------------------------------------
drop function if exists public.get_graph_membros(integer, boolean, integer);

-- ----------------------------------------------------------------------
-- 2) Função canônica do grafo (assinatura BIGINT).
-- ----------------------------------------------------------------------
create or replace function public.get_graph_membros(
  p_faccao_id  bigint   default null,
  p_include_co boolean  default true,
  p_max_pairs  integer  default 8000
) returns jsonb
language sql
stable
security invoker
as $$
with params as (
  select nullif(p_faccao_id, 0)::bigint as fid
),

-- 1) arestas diretas
direct_edges as (
  -- membro -> facção
  select m.membro_id::text source, m.faccao_id::text target,
         3.0::float weight, 'PERTENCE_A'::text relation
  from public.membros m
  join params p on true
  where m.faccao_id is not null
    and (p.fid is null or m.faccao_id = p.fid)

  union all
  -- membro -> função
  select m.membro_id::text, m.funcao_id::text, 3.0::float, 'EXERCE'
  from public.membros m
  join params p on true
  where m.funcao_id is not null
    and (p.fid is null or m.faccao_id = p.fid)

  union all
  -- função -> facção
  select f.funcao_id::text, f.faccao_id::text, 2.0::float, 'FUNCAO_DA_FACCAO'
  from public.funcoes f
  join params p on true
  where f.faccao_id is not null
    and (p.fid is null or f.faccao_id = p.fid)
),

-- 2) pares inferidos (co-ocorrências limitadas)
co_faccao as (
  select m1.membro_id::text source, m2.membro_id::text target,
         0.5::float weight, 'CO_FACCAO'::text relation
  from public.membros m1
  join public.membros m2
    on m1.faccao_id = m2.faccao_id
   and m1.membro_id < m2.membro_id
  join params p on true
  where (p.fid is null or m1.faccao_id = p.fid)
  limit p_max_pairs
),
co_funcao as (
  select m1.membro_id::text source, m2.membro_id::text target,
         0.8::float weight, 'CO_FUNCAO'::text relation
  from public.membros m1
  join public.membros m2
    on m1.funcao_id = m2.funcao_id
   and m1.funcao_id is not null
   and m1.membro_id < m2.membro_id
  join params p on true
  where (p.fid is null or m1.faccao_id = p.fid)
  limit p_max_pairs
),

edges as (
  select * from direct_edges
  union all
  select * from co_faccao where p_include_co
  union all
  select * from co_funcao where p_include_co
),

-- 3) nós — label de membro NUNCA usa membro_id.
--    alcunha é varchar[] no mondaha: pega o 1º valor não-vazio do array
--    (via array_to_string), com fallback seguro para nome_completo.
member_nodes as (
  select
    m.membro_id::text as id,
    coalesce(
      nullif(btrim(array_to_string(m.alcunha, ', ')), ''),
      nullif(btrim(coalesce(m.nome_completo::text, '')), ''),
      'Sem nome'
    )::text as label,
    'membro'::text as type,
    m.faccao_id,
    m.funcao_id
  from public.membros m
  join params p on true
  where (p.fid is null or m.faccao_id = p.fid)
),

faccao_nodes as (
  select
    f.faccao_id::text as id,
    coalesce(
      nullif(btrim(coalesce(f.nome::text, '')), ''),
      'Facção '||f.faccao_id::text
    )::text as label,
    'faccao'::text as type,
    f.faccao_id,
    null::bigint as funcao_id
  from public.faccoes f
  join params p on true
  where (p.fid is null or f.faccao_id = p.fid)
),

funcao_nodes as (
  select
    fu.funcao_id::text as id,
    coalesce(
      nullif(btrim(coalesce(fu.nome::text, '')), ''),
      'Função '||fu.funcao_id::text
    )::text as label,
    'funcao'::text as type,
    fu.faccao_id,
    fu.funcao_id
  from public.funcoes fu
  join params p on true
  where (p.fid is null or fu.faccao_id = p.fid)
),

base_nodes as (
  select * from member_nodes
  union all
  select * from faccao_nodes
  union all
  select * from funcao_nodes
),

deg as (
  select n.id, count(*)::int as degree
  from base_nodes n
  left join edges e on e.source = n.id or e.target = n.id
  group by n.id
),

nodes_json as (
  select jsonb_build_object(
    'id', n.id,
    'label', n.label,
    'type', n.type,
    'group', coalesce(n.faccao_id, 0),
    'size', greatest(10, 10 + ln(coalesce(d.degree,0)+1) * 8)
  ) j
  from base_nodes n
  left join deg d on d.id = n.id
),

edges_json as (
  select jsonb_build_object(
    'source', source, 'target', target,
    'weight', weight, 'relation', relation
  ) j
  from edges
)

select jsonb_build_object(
  'nodes', (select coalesce(jsonb_agg(j),'[]'::jsonb) from nodes_json),
  'edges', (select coalesce(jsonb_agg(j),'[]'::jsonb) from edges_json)
);
$$;

-- ----------------------------------------------------------------------
-- 3) Índices de suporte (idempotentes).
-- ----------------------------------------------------------------------
create index if not exists ix_membros_faccao_membro on public.membros (faccao_id, membro_id);
create index if not exists ix_membros_funcao_membro on public.membros (funcao_id, membro_id);
create index if not exists ix_funcoes_faccao        on public.funcoes (faccao_id);

-- ----------------------------------------------------------------------
-- 4) Atualiza estatísticas do planejador.
-- ----------------------------------------------------------------------
analyze public.membros;
analyze public.funcoes;
analyze public.faccoes;
