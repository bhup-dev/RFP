import { getFromRoot } from './apiClient'

export const getHealth = (signal) => getFromRoot('/health', { signal })

export const getReadiness = (signal) => getFromRoot('/health/ready', { signal })
