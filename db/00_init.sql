-- db/00_init.sql
create table
if not exists public.faccoes
(
  faccao_id bigserial primary key,
  nome text not null
);

create table
if not exists public.funcoes
(
  funcao_id bigserial primary key,
  nome text not null,
  faccao_id bigint references public.faccoes
(faccao_id)
);

create table
if not exists public.membros
(
  membro_id bigserial primary key,
  nome_completo text,
  alcunha text,
  faccao_id bigint references public.faccoes
(faccao_id),
  funcao_id bigint references public.funcoes
(funcao_id)
);

-- seed simples
insert into public.faccoes
    (nome)
values
    ('Alfa'),
    ('Bravo')
on conflict do nothing;
insert into public.funcoes
    (nome, faccao_id)
values
    ('Líder', 1),
    ('Operacional', 1),
    ('Líder', 2)
on conflict do nothing;
insert into public.membros
    (nome_completo, alcunha, faccao_id, funcao_id)
values
    ('Maria Silva', 'Maria', 1, 1),
    ('João Souza', 'João', 1, 2),
    ('Ana Lima', null, 2, 3)
on conflict do nothing;

-- Função get_graph_membros (segura com CASE …::text)
create or replace function public.get_graph_membros
(
  p_faccao_id bigint default null,
  p_include_co boolean default true,
  p_max_pairs int default 20000
) returns jsonb
language sql
stable
security definer
set search_path
= public, pg_temp
as $$
with
    direct_edges
    as
    (
        select m.membro_id::text source, m.faccao_id::text target, 3.0
    ::float weight, 'PERTENCE_A'::text relation
  from public.membros m
  where m.faccao_id is not null and
(p_faccao_id is null or m.faccao_id = p_faccao_id)
  union all
select m.membro_id::text, m.funcao_id::text, 3.0
::float, 'EXERCE'
  from public.membros m
  where m.funcao_id is not null and
(p_faccao_id is null or m.faccao_id = p_faccao_id)
  union all
select f.funcao_id::text, f.faccao_id::text, 2.0
::float, 'FUNCAO_DA_FACCAO'
  from public.funcoes f
  where f.faccao_id is not null and
(p_faccao_id is null or f.faccao_id = p_faccao_id)
),
co_faccao as
(
  select m1.membro_id::text source, m2.membro_id::text target, 0.5
::float weight, 'CO_FACCAO'::text relation
  from public.membros m1
  join public.membros m2 on m1.faccao_id = m2.faccao_id and m1.membro_id < m2.membro_id
  where
(p_faccao_id is null or m1.faccao_id = p_faccao_id)
  limit p_max_pairs
),
co_funcao as
(
  select m1.membro_id::text source, m2.membro_id::text target, 0.8::float weight, 'CO_FUNCAO'
::text relation
  from public.membros m1
  join public.membros m2 on m1.funcao_id = m2.funcao_id and m1.funcao_id is not null and m1.membro_id < m2.membro_id
  where
(p_faccao_id is null or m1.faccao_id = p_faccao_id)
  limit p_max_pairs
),
edges as
(
    select *
    from direct_edges
union all
    select *
    from co_faccao
    where p_include_co
union all
    select *
    from co_funcao
    where p_include_co
)
,
base_nodes as
(
  select
    m.membro_id::text as id,
    (case
      when coalesce(nullif(m.alcunha,''), nullif(m.nome_completo,'')) is null then ('ID '||m.membro_id::text)
      when nullif(m.alcunha,'') is not null then m.alcunha
      else m.nome_completo
    end)
::text as label,
    'membro'::text as type,
    m.faccao_id, m.funcao_id
  from public.membros m
  where
(p_faccao_id is null or m.faccao_id = p_faccao_id)
  union all
select f.faccao_id::text, coalesce(nullif(f.nome,''), 'Facção '||f.faccao_id::text)
::text, 'faccao', f.faccao_id, null
  from public.faccoes f
  where
(p_faccao_id is null or f.faccao_id = p_faccao_id)
  union all
select fu.funcao_id::text, coalesce(nullif(fu.nome,''), 'Função '||fu.funcao_id::text)
::text, 'funcao', fu.faccao_id, fu.funcao_id
  from public.funcoes fu
  where
(p_faccao_id is null or fu.faccao_id = p_faccao_id)
),
deg as
(
  select n.id, count(*)
::int as degree
  from base_nodes n left join edges e on e.source = n.id or e.target = n.id
  group by n.id
),
nodes_json as
(
  select jsonb_build_object(
    'id', n.id, 'label', n.label, 'type', n.type,
    'group', coalesce(n.faccao_id,0),
    'size', greatest(10, 10 + ln(coalesce(d.degree,0)+1) * 8)
  ) j
from base_nodes n left join deg d on d.id = n.id
)
,
edges_json as
(
  select jsonb_build_object('source',source,'target',target,'weight',weight,'relation',relation) j
from edges
)
select jsonb_build_object(
  'nodes', (select coalesce(jsonb_agg(j),'[]'
::jsonb) from nodes_json),
  'edges',
(select coalesce(jsonb_agg(j),'[]'
::jsonb) from edges_json)
);
$$;
