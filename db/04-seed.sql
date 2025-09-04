insert into faccoes (faccao_id, nome) values
  (6, 'Facção 6 (teste)') on conflict do nothing;

insert into funcoes (funcao_id, nome, faccao_id) values
  (1, 'Presidente', 6),
  (2, 'Vice-Presidente', 6),
  (6, 'Conselho Final do Progresso Geral', 6)
on conflict do nothing;

insert into membros (membro_id, nome_completo, alcunha, faccao_id, funcao_id) values
  (209, 'João da Silva', ARRAY['joaozinho'], 6, 4),
  (212, null, ARRAY['BIGULINHA'], 6, 6),
  (213, null, ARRAY['GRILO'], 6, null),
  (223, null, ARRAY['RARIDADE'], 6, null)
on conflict do nothing;
