import { createContext } from 'react'

export const ReviewAuthContext = createContext({
  auth: null,
  setCredentials: () => {},
  clearCredentials: () => {},
})
