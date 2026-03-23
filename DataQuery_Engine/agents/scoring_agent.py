import pandas as pd
import sys
import os
from typing import Dict, List

class ScoringAgent:
    """
    Agent for scoring search results by matching Original_URL with Result_URLs
    and assigning ranks based on the position where matches are found.
    """
    
    def __init__(self):
        pass
    
    def score_search_results(self, csv_file: str, output_file: str = None) -> str:
        """
        Score search results by matching Original_URL with Result_URLs.
        Assigns rank based on the position where the match is found.
        
        Args:
            csv_file: Path to the search results CSV file
            output_file: Path to save the scored results (optional)
            
        Returns:
            Path to the output file
        """
        print(f"📊 Loading search results from: {csv_file}")
        
        # Read the CSV file
        try:
            df = pd.read_csv(csv_file)
            print(f"✅ Loaded {len(df)} rows")
        except Exception as e:
            print(f"❌ Error reading CSV file: {e}")
            return None
        
        # Create rank column
        df['Rank'] = ''
        
        # Forward fill the Query_ID and Original_URL for grouped rows
        df['Query_ID'] = df['Query_ID'].ffill()
        df['Original_URL'] = df['Original_URL'].ffill()
        
        total_matches = 0
        unique_queries = df['Query_ID'].dropna().unique()
        
        print(f"\n🔍 Processing {len(unique_queries)} queries...")
        
        # Process each query group
        for query_id in unique_queries:
            query_group = df[df['Query_ID'] == query_id]
            
            if len(query_group) == 0:
                continue
                
            # Get the original URL for this query
            original_url = None
            for _, row in query_group.iterrows():
                if pd.notna(row['Original_URL']) and str(row['Original_URL']).strip():
                    original_url = str(row['Original_URL']).strip()
                    break
            
            if not original_url or original_url == 'nan':
                print(f"⚠️  Query {query_id}: No valid Original_URL found")
                continue
            
            print(f"📝 Query {query_id}: Checking {len(query_group)} results against original URL")
            
            # Check each result in this query group
            match_found = False
            for index, row in query_group.iterrows():
                result_url = str(row['Result_URL']).strip()
                result_rank = row['Result_Rank']
                
                # If URLs match exactly, assign the rank
                if (original_url == result_url and 
                    result_url != 'nan' and 
                    result_url != '' and
                    pd.notna(result_rank)):
                    
                    df.at[index, 'Rank'] = f"rank{int(result_rank)}"
                    match_found = True
                    total_matches += 1
                    print(f"  ✅ Match found at position {int(result_rank)}")
                    break  # Only one match per query expected
            
            if not match_found:
                print(f"  ❌ No match found for query {query_id}")
        
        # Set output file path
        if output_file is None:
            base_name = csv_file.replace('.csv', '')
            output_file = f"{base_name}_scored.csv"
        
        # Save the scored results
        try:
            df.to_csv(output_file, index=False)
            print(f"\n✅ Scored results saved to: {output_file}")
            print(f"📈 Total matches found: {total_matches}")
            
            # Show ranking distribution
            if total_matches > 0:
                rank_counts = df[df['Rank'] != '']['Rank'].value_counts().sort_index()
                print("\n📊 Ranking distribution:")
                for rank, count in rank_counts.items():
                    print(f"  {rank}: {count} matches")
            
            return output_file
            
        except Exception as e:
            print(f"❌ Error saving scored results: {e}")
            return None
    
    def analyze_scoring_results(self, csv_file: str):
        """
        Analyze the scoring results and provide insights.
        
        Args:
            csv_file: Path to the scored results CSV file
        """
        try:
            df = pd.read_csv(csv_file)
            
            print(f"\n📊 Scoring Analysis")
            print("=" * 50)
            
            # Basic statistics
            total_queries = df['Query_ID'].nunique()
            total_results = len(df)
            matches_found = len(df[df['Rank'] != ''])
            
            print(f"Total queries: {total_queries}")
            print(f"Total search results: {total_results}")
            print(f"URL matches found: {matches_found}")
            print(f"Match rate: {(matches_found/total_queries)*100:.1f}%")
            
            # Ranking performance
            if matches_found > 0:
                print(f"\n🏆 Ranking Performance:")
                rank_counts = df[df['Rank'] != '']['Rank'].value_counts().sort_index()
                
                for rank, count in rank_counts.items():
                    rank_num = int(rank.replace('rank', ''))
                    percentage = (count / matches_found) * 100
                    print(f"  Position {rank_num}: {count} matches ({percentage:.1f}%)")
                
                # Calculate average rank
                ranks = []
                for rank in df[df['Rank'] != '']['Rank']:
                    if isinstance(rank, str) and rank.startswith('rank'):
                        ranks.append(int(rank.replace('rank', '')))
                
                if ranks:
                    avg_rank = sum(ranks) / len(ranks)
                    print(f"\n📈 Average rank position: {avg_rank:.2f}")
                    
                    # Quality metrics
                    top_3_matches = sum(1 for rank in ranks if rank <= 3)
                    top_5_matches = sum(1 for rank in ranks if rank <= 5)
                    
                    print(f"📈 Top 3 performance: {top_3_matches}/{matches_found} ({(top_3_matches/matches_found)*100:.1f}%)")
                    print(f"📈 Top 5 performance: {top_5_matches}/{matches_found} ({(top_5_matches/matches_found)*100:.1f}%)")
                
        except Exception as e:
            print(f"❌ Error analyzing scoring results: {e}")

    def get_query_scores(self, csv_file: str) -> Dict:
        """
        Get individual query scores and performance metrics.
        
        Args:
            csv_file: Path to the scored results CSV file
            
        Returns:
            Dictionary with query-level scoring information
        """
        try:
            df = pd.read_csv(csv_file)
            query_scores = {}
            
            for query_id in df['Query_ID'].dropna().unique():
                query_group = df[df['Query_ID'] == query_id]
                matches = query_group[query_group['Rank'] != '']
                
                if len(matches) > 0:
                    rank = matches.iloc[0]['Rank']
                    if isinstance(rank, str) and rank.startswith('rank'):
                        rank_position = int(rank.replace('rank', ''))
                        original_title = query_group.iloc[0]['Original_Title']
                        
                        query_scores[query_id] = {
                            'original_title': original_title,
                            'rank_found': rank,
                            'rank_position': rank_position,
                            'matched': True
                        }
                    else:
                        original_title = query_group.iloc[0]['Original_Title'] if len(query_group) > 0 else 'Unknown'
                        query_scores[query_id] = {
                            'original_title': original_title,
                            'rank_found': None,
                            'rank_position': None,
                            'matched': False
                        }
                else:
                    original_title = query_group.iloc[0]['Original_Title'] if len(query_group) > 0 else 'Unknown'
                    query_scores[query_id] = {
                        'original_title': original_title,
                        'rank_found': None,
                        'rank_position': None,
                        'matched': False
                    }
            
            return query_scores
            
        except Exception as e:
            print(f"❌ Error getting query scores: {e}")
            return {}
        
        for query_id in unique_queries:
            query_group = df[df['Query_ID'] == query_id]
            
            if len(query_group) == 0:
                continue
            
            # Get the original URL for this query
            original_url = str(query_group.iloc[0]['Original_URL']).strip()
            query_text = str(query_group.iloc[0]['Generated_Query']).strip()
            
            if original_url == 'nan' or original_url == '':
                print(f"⚠️  Query {query_id}: No original URL found")
                continue
            
            print(f"\n📝 Query {query_id}: '{query_text}'")
            print(f"🎯 Original URL: {original_url}")
            
            match_found = False
            
            # Check each result in this query group
            for index, row in query_group.iterrows():
                result_url = str(row['Result_URL']).strip()
                result_rank = row['Result_Rank']
                
                # If URLs match exactly, assign the relevance score
                if original_url == result_url and result_url != 'nan' and result_url != '':
                    df.at[index, 'URL_Match_Rank'] = result_rank
                    # Calculate relevance score (higher score for better rank)
                    relevance_score = self._calculate_relevance_score(result_rank)
                    df.at[index, 'Relevance_Score'] = relevance_score
                    df.at[index, 'Match_Status'] = 'EXACT_MATCH'
                    
                    print(f"✅ MATCH FOUND at rank {result_rank} - Relevance Score: {relevance_score}")
                    match_found = True
                    total_matches += 1
                else:
                    df.at[index, 'Match_Status'] = 'NO_MATCH'
            
            if not match_found:
                print(f"❌ No matches found for this query")
        
        print(f"\n📊 Scoring Summary:")
        print(f"Total matches found: {total_matches}")
        print(f"Queries with matches: {len(df[df['URL_Match_Rank'] != ''])}")
        
        return df
    
    def _calculate_relevance_score(self, rank: int) -> float:
        """
        Calculate relevance score based on rank position.
        Higher ranks (1, 2, 3) get higher relevance scores.
        
        Args:
            rank: Position rank (1-10)
            
        Returns:
            Relevance score (0-1)
        """
        if rank <= 0:
            return 0.0
        
        # Score calculation: 1.0 for rank 1, decreasing for lower ranks
        # Rank 1 = 1.0, Rank 2 = 0.9, Rank 3 = 0.8, etc.
        score = max(0.0, 1.0 - (rank - 1) * 0.1)
        return round(score, 2)
    
    def generate_scoring_report(self, df: pd.DataFrame) -> Dict:
        """
        Generate a comprehensive scoring report.
        
        Args:
            df: DataFrame with scoring results
            
        Returns:
            Dictionary containing scoring statistics
        """
        if df.empty:
            return {}
        
        # Filter matches only
        matches_df = df[df['URL_Match_Rank'] != '']
        
        if matches_df.empty:
            return {
                'total_queries': len(df['Query_ID'].dropna().unique()),
                'total_results': len(df),
                'matches_found': 0,
                'match_rate': 0.0,
                'average_rank': 0.0,
                'average_relevance_score': 0.0,
                'rank_distribution': {}
            }
        
        # Calculate statistics
        total_queries = len(df['Query_ID'].dropna().unique())
        queries_with_matches = len(matches_df['Query_ID'].dropna().unique())
        match_rate = (queries_with_matches / total_queries) * 100 if total_queries > 0 else 0
        
        # Rank distribution
        rank_distribution = matches_df['URL_Match_Rank'].value_counts().sort_index().to_dict()
        
        # Average metrics
        avg_rank = matches_df['URL_Match_Rank'].mean()
        avg_relevance_score = matches_df['Relevance_Score'].mean()
        
        report = {
            'total_queries': total_queries,
            'total_results': len(df),
            'matches_found': len(matches_df),
            'queries_with_matches': queries_with_matches,
            'match_rate': round(match_rate, 2),
            'average_rank': round(avg_rank, 2),
            'average_relevance_score': round(avg_relevance_score, 2),
            'rank_distribution': rank_distribution
        }
        
        return report
    
    def save_scored_results(self, df: pd.DataFrame, output_path: str) -> bool:
        """
        Save the scored results to a CSV file.
        
        Args:
            df: DataFrame with scoring results
            output_path: Path to save the output CSV
            
        Returns:
            True if successful, False otherwise
        """
        try:
            df.to_csv(output_path, index=False)
            print(f"✅ Scored results saved to: {output_path}")
            return True
        except Exception as e:
            print(f"❌ Error saving results: {e}")
            return False
    
    def print_scoring_report(self, report: Dict):
        """
        Print a formatted scoring report.
        
        Args:
            report: Dictionary containing scoring statistics
        """
        print("\n" + "="*60)
        print("📊 SEARCH RELEVANCE SCORING REPORT")
        print("="*60)
        
        if not report:
            print("❌ No data available for reporting")
            return
        
        print(f"📋 Total Queries Processed: {report['total_queries']}")
        print(f"📋 Total Search Results: {report['total_results']}")
        print(f"🎯 Matches Found: {report['matches_found']}")
        print(f"🎯 Queries with Matches: {report['queries_with_matches']}")
        print(f"📈 Match Rate: {report['match_rate']}%")
        
        if report['matches_found'] > 0:
            print(f"📊 Average Match Rank: {report['average_rank']}")
            print(f"⭐ Average Relevance Score: {report['average_relevance_score']}")
            
            print(f"\n📈 Rank Distribution:")
            for rank, count in sorted(report['rank_distribution'].items()):
                percentage = (count / report['matches_found']) * 100
                print(f"  Rank {rank}: {count} matches ({percentage:.1f}%)")
        
        print("="*60)
