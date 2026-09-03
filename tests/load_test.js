import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 20 }, // simulate ramp-up of traffic from 1 to 20 users over 30 seconds.
    { duration: '1m', target: 20 }, // stay at 20 users for 1 minute
    { duration: '30s', target: 0 }, // ramp-down to 0 users
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'], // 95% of requests must complete below 2s
    http_req_failed: ['rate<0.01'], // http errors should be less than 1%
  },
};

export default function () {
  const url = 'http://localhost:8001/v1/query';
  const payload = JSON.stringify({
    query: 'What are the main features?',
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post(url, payload, params);
  
  check(res, {
    'is status 200': (r) => r.status === 200,
    'has answer': (r) => r.json().hasOwnProperty('answer'),
  });

  sleep(1);
}
