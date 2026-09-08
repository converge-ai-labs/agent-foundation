-- Only a committed terminal Run fact authorizes this unavailable-stream cleanup.
-- It never repairs history or grants publication authority, so an old primary
-- incarnation, missing Stream, or unfinished write cannot prevent retirement.
local stream, metadata = KEYS[1], KEYS[2]
local request = cjson.decode(ARGV[1])
local metadata_type = redis.call('TYPE', metadata).ok
if metadata_type == 'none' then
    if redis.call('EXISTS', stream) == 0 then return {'ok'} end
elseif metadata_type ~= 'hash' or
    redis.call('HGET', metadata, 'organization_id') ~= request.organization_id or
    redis.call('HGET', metadata, 'run_id') ~= request.run_id then
    error('RUN_STREAM_IDENTITY', 0)
end

-- Record one deadline before setting either TTL. Lost acknowledgements and
-- partially applied expiration must never extend retention on every retry.
local deadline = redis.call('HGET', metadata, 'retention_deadline') or
    tostring(tonumber(redis.call('TIME')[1]) + request.closed_ttl_seconds)
redis.call('HSET', metadata, 'organization_id', request.organization_id, 'run_id', request.run_id,
    'closed_at', request.closed_at, 'incomplete', '1', 'attempt_id', '', 'retention_deadline', deadline)
redis.call('HDEL', metadata, 'pending', 'pending_ids')
redis.call('EXPIREAT', stream, deadline)
redis.call('EXPIREAT', metadata, deadline)
return {'ok'}
