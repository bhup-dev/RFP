import { useContext } from 'react'
import { ReviewAuthContext } from './reviewAuth'

export function useReviewAuth() {
  return useContext(ReviewAuthContext)
}
