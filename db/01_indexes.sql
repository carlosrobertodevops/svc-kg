-- Se RLS ativo, crie policies USING (true) para select nessas tabelas p/ role svc_kg
-- db/01_indexes.sql
create index
if not exists ix_membros_faccao_membro on public.membros
(faccao_id, membro_id);
create index
if not exists ix_membros_funcao_membro on public.membros
(funcao_id, membro_id);
create index
if not exists ix_funcoes_faccao on public.funcoes
(faccao_id);
analyze public.membros; analyze public.funcoes; analyze public.faccoes;
