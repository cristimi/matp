/**
 * Chart engine registry — the single seam between the app and whichever chart
 * library is in use.
 *
 * To swap engines: add a folder under adapters/, implement ChartAdapter from
 * ./core, and change the one import below. Nothing in ./core and nothing in the
 * pages needs to change, because both depend only on the ChartAdapter interface.
 */
import { lightweightChartsAdapter } from './adapters/lightweightCharts';
import type { ChartAdapter } from './core';

export const chartAdapter: ChartAdapter = lightweightChartsAdapter;

export * from './core';
