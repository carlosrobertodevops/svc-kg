-- Função compatível com o serviço (retorna JSONB nodes/edges)
create or replace function public.get_graph_membros(
  p_faccao_id  bigint default null,
  p_include_co boolean default true,
  p_max_pairs  int    default 20000
)
returns jsonb
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with direct_edges as (
    -- membro -> facção
    select m.membro_id::text source,
           m.faccao_id::text target,
           3.0::float        weight,
           'PERTENCE_A'::text relation
    from membros m
    where m.faccao_id is not null
      and (p_faccao_id is null or m.faccao_id = p_faccao_id)

    union all
    -- membro -> função
    select m.membro_id::text, m.funcao_id::text, 3.0::float, 'EXERCE'
    from membros m
    where m.funcao_id is not null
      and (p_faccao_id is null or m.faccao_id = p_faccao_id)

    union all
    -- função -> facção
    select f.funcao_id::text, f.faccao_id::text, 2.0::float, 'FUNCAO_DA_FACCAO'
    from funcoes f
    where f.faccao_id is not null
      and (p_faccao_id is null or f.faccao_id = p_faccao_id)
  ),
  co_faccao as (
    select m1.membro_id::text source, m2.membro_id::text target,
           0.5::float weight, 'CO_FACCAO'::text relation
    from membros m1
    join membros m2
      on m1.faccao_id = m2.faccao_id
     and m1.membro_id < m2.membro_id
    where (p_faccao_id is null or m1.faccao_id = p_faccao_id)
    limit p_max_pairs
  ),
  co_funcao as (
    select m1.membro_id::text source, m2.membro_id::text target,
           0.8::float weight, 'CO_FUNCAO'::text relation
    from membros m1
    join membros m2
      on m1.funcao_id = m2.funcao_id
     and m1.funcao_id is not null
     and m1.membro_id < m2.membro_id
    where (p_faccao_id is null or m1.faccao_id = p_faccao_id)
    limit p_max_pairs
  ),
  edges as (
    select * from direct_edges
    union all select * from co_faccao where p_include_co
    union all select * from co_funcao where p_include_co
  ),
  base_nodes as (
    -- membros
    select
      m.membro_id::text as id,
      coalesce(
        nullif(array_to_string(m.alcunha, ', '), ''),           -- arruma text[] -> 'a, b'
        nullif(m.nome_completo, ''),
        'ID ' || m.membro_id::text
      ) as label,
      'membro'::text as type,
      m.faccao_id,
      m.funcao_id
    from membros m
    where (p_faccao_id is null or m.faccao_id = p_faccao_id)

    union all
    -- facções
    select f.faccao_id::text, f.nome, 'faccao', f.faccao_id, null
    from faccoes f
    where (p_faccao_id is null or f.faccao_id = p_faccao_id)

    union all
    -- funções
    select fu.funcao_id::text, fu.nome, 'funcao', fu.faccao_id, fu.funcao_id
    from funcoes fu
    where (p_faccao_id is null or fu.faccao_id = p_faccao_id)
  ),
  deg as (
    select n.id, count(*)::int degree
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

-- Permissões de execução p/ PostgREST
grant execute on function public.get_graph_membros(bigint, boolean, int) to anon, service_role;
