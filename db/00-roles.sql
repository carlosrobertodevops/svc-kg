-- roles básicas p/ PostgREST
create role anon nologin;
create role service_role nologin;

-- dono do banco já é o usuário "kg", criado via env.
grant usage on schema public to anon, service_role;
