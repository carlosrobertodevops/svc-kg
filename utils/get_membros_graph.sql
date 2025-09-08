-- ======================================================================
-- Função: public.get_graph_membros(p_faccao_id int, p_include_co boolean, p_max_pairs int)
-- Retorna: jsonb com { nodes: [...], edges: [...] }
-- Regras:
--   - Nós de membro: label = primeiro valor não vazio de alcunha ou nome_completo
--                    (funciona para text e para text[]), nunca exibe membro_id no label.
--   - Arestas e limites iguais ao seu modelo atual.
-- Atualização: 08/09/2025 17h51min
-- Fix PGRST203: remover sobrecarga e ficar só com bigint
-- ==============================================================

-- 1) Remova a versão antiga (se existir) que usa INTEGER
drop function if exists public.get_graph_membros(integer, boolean, integer);

-- 2) Crie/Recrie a versão canônica com BIGINT
create or replace function public.get_graph_membros(
  p_faccao_id  bigint,
  p_include_co boolean,
  p_max_pairs  integer
) returns jsonb
language sql
stable
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

-- 2) pares inferidos (limitados)
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

-- 3) nós (label de membro NUNCA usa membro_id, pega 1º de alcunha/nome)
member_nodes as (
  select
    m.membro_id::text as id,
    coalesce(
      nullif(btrim(
        case
          when jsonb_typeof(to_jsonb(m.alcunha)) = 'array'  then (to_jsonb(m.alcunha)->>0)
          when jsonb_typeof(to_jsonb(m.alcunha)) = 'string' then btrim(to_jsonb(m.alcunha)::text, '\"')
          else null
        end
      ), ''),
      nullif(btrim(
        case
          when jsonb_typeof(to_jsonb(m.nome_completo)) = 'array'  then (to_jsonb(m.nome_completo)->>0)
          when jsonb_typeof(to_jsonb(m.nome_completo)) = 'string' then btrim(to_jsonb(m.nome_completo)::text, '\"')
          else null
        end
      ), ''),
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

-- 3) Permissões (ajuste aos seus roles; com service key costuma usar service_role)
grant execute on function public.get_graph_membros(bigint, boolean, integer)
  to anon, authenticated, service_role;

-- 4) Força o PostgREST a recarregar o schema cache
-- (no Supabase funciona com o canal 'pgrst')
notify pgrst, 'reload schema';
