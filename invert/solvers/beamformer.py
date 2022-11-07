import numpy as np
import mne
from copy import deepcopy
from ..util import find_corner
from .base import BaseSolver, InverseOperator

class SolverMVAB(BaseSolver):
    ''' Class for the Minimum Variance Adaptive Beamformer (MVAB) inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    '''
    def __init__(self, name="Minimum Variance Adaptive Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        R_inv = np.linalg.inv(y@y.T)
        leadfield -= leadfield.mean(axis=0)
        
  
        
        inverse_operators = []
        for alpha in self.alphas:
            inverse_operator = 1/(leadfield.T @ R_inv @ leadfield + alpha * np.identity(n_dipoles)) @ leadfield.T @ R_inv
            # R_inv = np.linalg.inv(y@y.T + alpha * np.identity(n_chans))
            # inverse_operator = 1/(leadfield.T @ R_inv @ leadfield) @ leadfield.T @ R_inv
            # inverse_operator = 1/(leadfield.T @ R_inv @ leadfield + alpha * np.identity(n_dipoles)) @ leadfield.T @ R_inv

            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverLCMV(BaseSolver):
    ''' Class for the Linearly Constrained Minimum Variance Beamformer (LCMV) inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    '''
    def __init__(self, name="LCMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', weight_norm=True, verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.weight_norm = weight_norm
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)
        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)
        
        
        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)
            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]
                w = np.linalg.inv(l.T @ C_inv @ l ) * l.T @ C_inv
                W.append(w)
            W = np.stack(W, axis=1)[0].T
            if self.weight_norm:
                W = W / np.linalg.norm(W, axis=0)
            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverSMV(BaseSolver):
    ''' Class for the Standardized Minimum Variance (SMV) Beamformer inverse
    solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Jonmohamadi, Y., Poudel, G., Innes, C., Weiss, D., Krueger, R., & Jones, R.
    (2014). Comparison of beamformers for EEG source signal reconstruction.
    Biomedical Signal Processing and Control, 14, 175-188.
    '''
    def __init__(self, name="SMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)
        
        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)
        
        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)
            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]
                w = (C_inv @ l) / np.sqrt(l.T @ C_inv @ l)
                W.append(w)
            W = np.stack(W, axis=1)[:, :, 0]

            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverWNMV(BaseSolver):
    ''' Class for the Weight-normalized Minimum Variance (WNMV) Beamformer inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Jonmohamadi, Y., Poudel, G., Innes, C., Weiss, D., Krueger, R., & Jones, R.
    (2014). Comparison of beamformers for EEG source signal reconstruction.
    Biomedical Signal Processing and Control, 14, 175-188.
    '''
    def __init__(self, name="WNMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)

        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)
        
        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)
            C_inv_2 = np.linalg.inv(C_inv)
            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]
                w = (C_inv @ l) / np.sqrt(l.T @ C_inv_2 @ l)
                W.append(w)
            W = np.stack(W, axis=1)[:, :, 0]

            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverHOCMV(BaseSolver):
    ''' Class for the Higher-Order Covariance Minimum Variance (HOCMV) Beamformer inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Jonmohamadi, Y., Poudel, G., Innes, C., Weiss, D., Krueger, R., & Jones, R.
    (2014). Comparison of beamformers for EEG source signal reconstruction.
    Biomedical Signal Processing and Control, 14, 175-188.
    '''
    def __init__(self, name="HOCMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', order=3, verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)

        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)
        
        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)
            C_inv_n = deepcopy(C_inv)
            for _ in range(order-1):
                C_inv_n = np.linalg.inv(C_inv_n)

            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]
                w = (C_inv_n @ l) / (l.T @ C_inv_n @ l)
                W.append(w)
            W = np.stack(W, axis=1)[:, :, 0]

            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverESMV(BaseSolver):
    ''' Class for the Eigenspace-based Minimum Variance (ESMV) Beamformer inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Jonmohamadi, Y., Poudel, G., Innes, C., Weiss, D., Krueger, R., & Jones, R.
    (2014). Comparison of beamformers for EEG source signal reconstruction.
    Biomedical Signal Processing and Control, 14, 175-188.
    '''
    def __init__(self, name="ESMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)
        
        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)

        U, s, _ = np.linalg.svd(C)
        j = find_corner(np.arange(len(s)), s)

        Us = U[:, :j]
        Un = U[:, j:]

        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)

            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]
                w_mv = (C_inv @ l) / (l.T @ C_inv @ l)
                w_esmv = Us @ Us.T @ w_mv
                W.append(w_esmv)
            W = np.stack(W, axis=1)[:, :, 0]

            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverMCMV(BaseSolver):
    ''' Class for the Multiple Constrained Minimum Variance (MCMV) Beamformer
    inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Nunes, A. S., Moiseev, A., Kozhemiako, N., Cheung, T., Ribary, U., &
    Doesburg, S. M. (2020). Multiple constrained minimum variance beamformer
    (MCMV) performance in connectivity analyses. NeuroImage, 208, 116386.
    '''
    def __init__(self, name="MCMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)

        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)

        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)

            W = C_inv @ leadfield @ np.linalg.inv(leadfield.T @ C_inv @ leadfield)

            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

class SolverESMCMV(BaseSolver):
    ''' Class for the Eigenspace-Based Multiple Constrained Minimum Variance
    (ES-MCMV) Beamformer inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Nunes, A. S., Moiseev, A., Kozhemiako, N., Cheung, T., Ribary, U., &
    Doesburg, S. M. (2020). Multiple constrained minimum variance beamformer
    (MCMV) performance in connectivity analyses. NeuroImage, 208, 116386.

    Jonmohamadi, Y., Poudel, G., Innes, C., Weiss, D., Krueger, R., & Jones, R.
    (2014). Comparison of beamformers for EEG source signal reconstruction.
    Biomedical Signal Processing and Control, 14, 175-188.
    '''
    def __init__(self, name="ES-MCMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)

        # Recompute regularization based on the max eigenvalue of the Covariance
        # Matrix (opposed to that of the leadfield)
        C = y@y.T
        self.alphas = self.get_alphas(reference=C)

        U, s, _ = np.linalg.svd(C)
        j = find_corner(np.arange(len(s)), s)

        Us = U[:, :j]
        Un = U[:, j:]

        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(C + alpha * I)
            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]

                w_mv = C_inv @ l *(1 / (l.T @ C_inv @ l))
                w_esmv = C_inv @ Us @ Us.T @ w_mv
                W.append(w_esmv)
            W = C_inv @ leadfield @ np.linalg.inv(leadfield.T @ C_inv @ leadfield)

            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)

    

class SolverMCMV(BaseSolver):
    ''' Class for the Multiple Constrained Minimum Variance (MCMV) Beamformer
    inverse solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    Nunes, A. S., Moiseev, A., Kozhemiako, N., Cheung, T., Ribary, U., &
    Doesburg, S. M. (2020). Multiple constrained minimum variance beamformer
    (MCMV) performance in connectivity analyses. NeuroImage, 208, 116386.

    '''
    def __init__(self, name="MCMV Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', weight_norm=True, verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.weight_norm = weight_norm
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)
  
        
        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(y@y.T + alpha * I)
            W = C_inv @ leadfield @ np.linalg.inv(leadfield.T @ C_inv @ leadfield)
            
            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)
    

class SolverSAM(BaseSolver):
    ''' Class for the Synthetic Aperture Magnetometry Beamformer (SAM) inverse
    solution.
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    '''
    def __init__(self, name="SAM Beamformer", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, evoked, *args, alpha='auto', weight_norm=True, verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        self.weight_norm = weight_norm
        self.forward = forward
        leadfield = self.forward['sol']['data']
        leadfield -= leadfield.mean(axis=0)
        n_chans, n_dipoles = leadfield.shape
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)

        y = evoked.data
        y -= y.mean(axis=0)
        leadfield -= leadfield.mean(axis=0)
        I = np.identity(n_chans)
  
        
        inverse_operators = []
        for alpha in self.alphas:
            C_inv = np.linalg.inv(y@y.T + alpha * I)
            W = []
            for i in range(n_dipoles):
                l = leadfield[:, i][:, np.newaxis]
                w = (C_inv@l) / (l.T@C_inv@l)
                W.append(w)
            W = np.stack(W, axis=1)[:, :, 0]
            if self.weight_norm:
                W = W / np.linalg.norm(W, axis=0)
            inverse_operator = W.T
            inverse_operators.append(inverse_operator)

        self.inverse_operators = [InverseOperator(inverse_operator, self.name) for inverse_operator in inverse_operators]
        return self

    def apply_inverse_operator(self, evoked) -> mne.SourceEstimate:
        return super().apply_inverse_operator(evoked)