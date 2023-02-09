# Computing total time for notifications

Make a .env file. Copy paste the env from LastPass (Staging / priority lanes perf test)

## Running the tests

Note first that there are a few sensible defaults set in `locust.conf`. In particular, to run with the gui you will have to override the `headless = true` setting

### Posts to /email

`tasks_individual_emails.py` will run 20 times as many bulk priority messages as high prioirty.
You can see and change the different weightings accordingly in priority-bulk_individal_emails.py

In its current state 37 users sends around 2000 emails a min, and 67 will send 4000.
Run both tests to see what high and normal load does to the application.

```
locust -f ./tasks_individual_emails.py --run-time=10m --users=37 --ref=perf0524z-email
```
will POST approximately 2000 bulk emails per minute and 100 priority emails per minute for 10 minutes.

### Posts to /bulk

`tasks_bulk_endpoint.py` will POST a file to bulk of size `JOB_SIZE` (default 10) every 10 seconds (per user). Best to run it with one user, and terminate after the desired number of POSTs have gone through.

```
locust -f ./tasks_bulk_endpoint.py --run-time=15s --users=1 --ref=perf0524z-bulk
```
will POST to `/bulk` twice

The tests add the current time to the notification's `reference` or the job's `name` when making the POST. We can use that to compute the total time from POST to delivery receipt:

If you use the suffixes "-email" and "-bulk" for your reference / name then you can get statistics using the following Hasura query:

```sql
WITH
    ref AS (VALUES ('perf0524z')),

bulk_initial_data as (
    select 
        to_timestamp(split_part(j.original_file_name, ' ', 1), 'YYYY-MM-DD HH24:MI:SS.US') as posted_at,
        n.created_at, n.sent_at, n.updated_at, notification_status as status, t.process_type as priority
    from notifications n join templates t on n.template_id = t.id
    join jobs j on n.job_id = j.id
    where j.original_file_name like concat('%', (table ref), '-bulk%')
),
bulk_data as (
    select *,
    EXTRACT(epoch FROM updated_at - posted_at) as total_time,
    EXTRACT(epoch FROM created_at - posted_at) as redis_time,
    EXTRACT(epoch FROM sent_at - created_at) as processing_time,
    EXTRACT(epoch FROM updated_at - sent_at) as delivery_time
    from bulk_initial_data
),
bulk_stats as ( 
    select 
        '/bulk' endpoint,
        status, priority, count(*),
        percentile_cont(0.5) within group(order by redis_time) AS redis_median,
        percentile_cont(0.5) within group(order by processing_time) AS processing_median,
        percentile_cont(0.5) within group(order by delivery_time) AS delivery_median
    from bulk_data
    group by priority, status
),
email_initial_data as (
    select 
        to_timestamp(split_part(client_reference, ' ', 1), 'YYYY-MM-DD HH24:MI:SS.US') as posted_at,
        n.created_at, n.sent_at, n.updated_at, client_reference, notification_status as status, t.process_type as priority
    from notifications n join templates t on n.template_id = t.id
    where client_reference like concat('%', (table ref), '-email%')
),
email_data as (
    select *,
    EXTRACT(epoch FROM updated_at - posted_at) as total_time,
    EXTRACT(epoch FROM created_at - posted_at) as redis_time,
    EXTRACT(epoch FROM sent_at - created_at) as processing_time,
    EXTRACT(epoch FROM updated_at - sent_at) as delivery_time
    from email_initial_data
),
email_stats as (
    select 
        '/email' endpoint,
        status, priority, count(*),
        percentile_cont(0.5) within group(order by redis_time) AS redis_median,
        percentile_cont(0.5) within group(order by processing_time) AS processing_median,
        percentile_cont(0.5) within group(order by delivery_time) AS delivery_median
    from email_data
    group by priority, status
)
select * from email_stats 
union all
select * from bulk_stats
```

### Posts to /sms

Similarly you can test sms with a command similar to
```
locust -f ./tasks_individual_sms.py  --run-time=10m --users=20 --ref=perf_sms_0112-aa
```
To see the timings, run the SQL
```sql
WITH
    ref AS (VALUES ('perf_sms_0112-aa')),

initial_data as (
    select 
        n.created_at, n.sent_at, n.updated_at, client_reference, notification_status as status, t.process_type as priority
    from notifications n join templates t on n.template_id = t.id
    where client_reference like concat('%', (table ref), '%')
),
data as (
    select *,
    EXTRACT(epoch FROM updated_at - created_at) as total_time,
    EXTRACT(epoch FROM sent_at - created_at) as processing_time,
    EXTRACT(epoch FROM updated_at - sent_at) as delivery_time
    from initial_data
),
stats as (
    select 
        '/sms' endpoint,
        status, priority, count(*),
        percentile_cont(0.5) within group(order by total_time) AS total_median,
        percentile_cont(0.5) within group(order by processing_time) AS processing_median,
        percentile_cont(0.5) within group(order by delivery_time) AS delivery_median
    from data
    group by priority, status
)
select * from stats 
```
