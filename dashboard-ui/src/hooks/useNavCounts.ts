import { useState, useEffect } from 'react';
import { api } from '../api';

export function useNavCounts() {
  const [counts, setCounts] = useState({
    strategies: { active: 0, inactive: 0 },
    positions: { open: 0, closed: 0, stale: 0 },
    orders: { filled: 0, failed: 0 }
  });

  const fetchCounts = async () => {
    try {
      const [strategies, positions, orders] = await Promise.all([
        api.get<any[]>('/strategies'),
        api.get<any[]>('/positions'),
        api.get<any>('/orders') 
      ]);

      const orderItems = orders.items || [];

      setCounts({
        strategies: {
          active: strategies.filter(s => s.enabled).length,
          inactive: strategies.filter(s => !s.enabled).length
        },
        positions: {
          open: positions.filter(p => p.status === 'open').length,
          closed: positions.filter(p => p.status === 'closed').length,
          stale: positions.filter(p => p.status === 'stale').length
        },
        orders: {
          filled: orderItems.filter((o: any) => o.status === 'filled').length || 0,
          failed: orderItems.filter((o: any) => o.status === 'route_failed' || o.status === 'failed').length || 0
        }
      });
    } catch (e) {
      console.error('Failed to fetch nav counts', e);
    }
  };

  useEffect(() => {
    fetchCounts();
    const interval = setInterval(fetchCounts, 30000);
    return () => clearInterval(interval);
  }, []);

  return counts;
}
