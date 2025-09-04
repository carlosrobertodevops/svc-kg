-- Alias para manter compatibilidade com quem chama et_graph_membros
create or replace function public.et_graph_membros
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
select public.get_graph_membros(p_faccao_id, p_include_co, p_max_pairs);
$$;
