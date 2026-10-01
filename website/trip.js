// Runs before the first paint. The page is prerendered for the primary trip; when a
// link names a trip, its texts stay hidden until app.js has filled in that trip.
try {
  const trip=new URLSearchParams(location.search).get('trip');
  if(trip)document.documentElement.dataset.tripLoading='';
} catch {}
