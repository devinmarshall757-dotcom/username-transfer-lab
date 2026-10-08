"""Bounded scheduled retries in the local browser lab only."""
from run_browser_lab import BrowserPlatform


class RetryBrowserPlatform(BrowserPlatform):
    MAX_ATTEMPTS = 3
    DEADLINE_MS = 500

    def release(self, seller):
        import time
        self.started = True
        target = time.time() * 1000 + 150
        self.buyer.evaluate('''options=>{
            window.claimResult=null;window.retryTrace=[];
            window.pendingBuyer=(async()=>{
                const pause=ms=>new Promise(r=>setTimeout(r,ms));
                await pause(Math.max(0,options.target+options.offset-Date.now()));
                const deadline=performance.now()+options.deadline;
                const snapshot=await fetch(`/runs/${runId}`).then(r=>r.json());
                const interval=Math.max(20,snapshot.config.min_interval_ms);
                for(let attempt=1;attempt<=options.attempts;attempt++){
                    if(performance.now()>=deadline){window.retryTrace.push({stop:'deadline'});break;}
                    // Public ownership observation: stop after success/capture,
                    // including when the preceding response was ambiguous.
                    const before=await fetch(`/runs/${runId}`).then(r=>r.json());
                    if(performance.now()>=deadline){window.retryTrace.push({stop:'deadline'});break;}
                    if(before.owner===actor){window.retryTrace.push({stop:'verified_owner'});break;}
                    if(before.owner!==null&&before.owner!==before.seller){window.retryTrace.push({stop:'competitor_owner'});break;}
                    const sent=performance.now();
                    document.getElementById('profile').requestSubmit();
                    await window.pendingSubmission;
                    const result=window.claimResult;
                    window.retryTrace.push({attempt,sent_ms:sent,response:result});
                    if(result.accepted)break;
                    const after=await fetch(`/runs/${runId}`).then(r=>r.json());
                    if(after.owner===actor){window.retryTrace.push({stop:'verified_after_unknown'});break;}
                    if(after.owner!==null&&after.owner!==after.seller){window.retryTrace.push({stop:'competitor_owner'});break;}
                    if(result.status!==429&&result.status!==200){window.retryTrace.push({stop:'unknown_response'});break;}
                    if(attempt===options.attempts)break;
                    const wait=result.status===429?Math.max(interval,result.retry_ms||0):Math.max(0,interval-(performance.now()-sent));
                    if(performance.now()+wait>=deadline){window.retryTrace.push({stop:'deadline'});break;}
                    await pause(wait);
                }
            })();
        }''', {'target':target,'offset':self.offset,'attempts':self.MAX_ATTEMPTS,'deadline':self.DEADLINE_MS})
        self.seller.evaluate('''async target=>{
            await new Promise(r=>setTimeout(r,Math.max(0,target-Date.now())));
            document.getElementById('profile').requestSubmit();await window.pendingSubmission;
        }''', target + self.seller_error)
